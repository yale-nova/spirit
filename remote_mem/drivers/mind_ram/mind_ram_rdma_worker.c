// start from example at https://github.com/Panky-codes/blkram

#include "asm/page.h"
#include <linux/blk_types.h>
#include <linux/sysfb.h>
#include <linux/module.h>
#include <linux/blkdev.h>
#include <linux/blk-mq.h>
#include <linux/idr.h>
#include <linux/fs.h>
#include <linux/uaccess.h>
#include <linux/miscdevice.h>
#include <linux/delay.h>
#include <linux/debugfs.h>
#include <linux/atomic.h>
#include <linux/slab.h>
#include <linux/kthread.h>
#include "mind_ram_drv.h"
#include "mind_ram_drv_rdma.h"

// Define a kfifo for struct mind_io_request
kfifo_t_mind_io mind_io_request_queue;
extern int working_status;
extern spinlock_t entry_hashmap_lock;
extern DECLARE_HASHTABLE(mind_request_map, MIND_REQ_HASH_BUCKET_SHIFT);
atomic_t num_pending_rdma = ATOMIC_INIT(0);
static atomic_t num_pending_pages = ATOMIC_INIT(0);
static atomic_t num_served_pages = ATOMIC_INIT(0);
static atomic_t num_served_read_pages = ATOMIC_INIT(0);
extern spinlock_t page_stat_lock;
extern DECLARE_HASHTABLE(served_pages_map, MIND_PAGE_STAT_BUKCET_SHIFT);

static void wait_with_sleep(void)
{
	// udelay(WAIT_RESPONSE_TIME_IN_US);
	usleep_range(WAIT_RESPONSE_TIME_IN_US, WAIT_RESPONSE_TIME_IN_US);
}

#ifdef MIND_PRINT_QUEUE
static void debug_mind_print_buf_hex(void *buf, unsigned long len, int is_read)
{
	pr_info("Data - read[%d]\n", is_read);
	for (int i = 0; i < len; i += 16) {
		bool all_zeros = true;
		for (int j = i; j < i + 16 && j < len; j++) {
			if (*((u8 *)buf + j) != 0) {
				all_zeros = false;
				break;
			}
		}
		if (all_zeros)
			continue;
		pr_cont("\n%04x ", i);
		for (int j = i; j < i + 16 && j < len; j++) {
			pr_cont("%02x ", *((u8 *)buf + j));
		}
		pr_cont(" | ");
		for (int j = i; j < i + 16 && j < len; j++)
			pr_cont("%c", isprint(*((u8 *)buf + j)) ? *((u8 *)buf + j) : '.');
	}
	pr_cont("\n");
}
#endif

static struct request_map_entry *get_request_entry(__u64 rq)
{
	struct request_map_entry *entry = NULL;
	spin_lock(&entry_hashmap_lock);
	hash_for_each_possible(mind_request_map, entry, node, rq) {
	    if ((__u64)entry->rq == rq) {
	        // found the entry
	        break;
	    }
	}
	spin_unlock(&entry_hashmap_lock);
	return entry;
}

void account_page_stat(struct fault_task *task)
{
#ifdef MIND_PAGE_STATS
	// If the entry does not exist, create it
	struct page_stat_entry *page_stat = NULL;
	int found_entry = 0;
	spin_lock(&page_stat_lock);
	hash_for_each_possible(served_pages_map, page_stat, node, task->fault_va) {
		if (page_stat->va == task->fault_va) {
			// found the entry
			found_entry = 1;
			// increment the count
			page_stat->count++;
			break;
		}
	}
	if (!found_entry) {
		// create a new entry
		page_stat = kmalloc(sizeof(struct page_stat_entry), GFP_KERNEL);
		if (!page_stat) {
			pr_err_ratelimited("Failed to allocate memory for page_stat_entry\n");
		} else {
			page_stat->va = task->fault_va;
			page_stat->count = 1;
			hash_add(served_pages_map, &page_stat->node, task->fault_va);
		}
	}
	spin_unlock(&page_stat_lock);
#endif
}

void finish_mind_req(struct mind_rdma_req *mind_req)
{
	// TODO: retrieve task and request
    struct fault_task *task = (struct fault_task *)mind_req->task_va;
    struct request_map_entry *entry = (struct request_map_entry *)mind_req->entry;
	// unmap rm and remove mind req
	unmap_mind_req(mind_req);
	// now mind_req is NULL

	if (!task || !entry)
	{
		// likely the test
		pr_alert_ratelimited("finish_mind_req :: Skipping NULL task or entry :: tsk=0x%lx, ent=0x%lx\n",
			(unsigned long)task, (unsigned long)entry);
		return ;
	}
    // Single threaded request accounting
	atomic_inc(&num_served_pages);
	if (task->type == FAULT_ONLY)
	{
		atomic_inc(&num_served_read_pages);
		// Mark the page in served_pages_map
		account_page_stat(task);
	}
	atomic_dec(&num_pending_pages);
    if (entry->operations[task->op_index].status != REQ_STATUS_ACKED)
	{
		entry->operations[task->op_index].status = REQ_STATUS_ACKED;
		kfree(task);
		if (atomic_dec_return(&entry->num_pending))
		{
			// still pending operations
			return;
		}
    } else {
		pr_err_ratelimited("%s: The operation has been ACKed already\n", __func__);
		return;
	}

	// Now, we can end the request
	// - This is the only entity that can end the request (depending on num_pending)
	// - Others must not access 'entry' once they checked num_pending
	// - For now, since this is the only thread, we the above may not be required
    blk_mq_end_request(entry->rq, BLK_STS_OK);
	// remove it from the hashmap
	spin_lock(&entry_hashmap_lock);
	hash_del(&entry->node);
	kfree(entry);
	spin_unlock(&entry_hashmap_lock);
}

// NOTE: user space must prioritize free up kernel -> user queue.
// Here, we opportunistically serve the requests (if there is any)
// @return: -1 if queue is empty, 0 if no task is served, 1 if a task is served
static int serve_acks(void)
{
	int ret = -1;
	struct request_map_entry *entry = NULL;
	struct fault_task *task = NULL;
	// POLL until the task is processed
	int poll_count = 0;

	struct mind_rdma_req *mind_req = NULL;
	while (poll_count < MIND_POLL_RETRY_CNT)
	{
		mind_req = poll_cq();
		if (mind_req)
		{
			break;
		}
		poll_count++;
	}
	if (!mind_req)
	{
		// pr_err_ratelimited("%s: Queue is empty, task has not been provided by the user space\n", __func__);
		return -1;
	}
	atomic_dec(&num_pending_rdma);

	// Find the corresponding request and serve it
	task = (struct fault_task *)mind_req->task_va;
	if (!mind_req->entry || !task)
	{
		pr_err_ratelimited(
			"%s: Cannot find the request entr :: entry: 0x%lx, req->entry: 0x%lx, task: 0x%lx\n",
			__func__, (unsigned long)entry, (unsigned long)mind_req->entry, (unsigned long)task);
		return 0;
	}
	entry = get_request_entry((__u64)mind_req->entry->rq);
	if (!entry || entry != mind_req->entry)
	{
		pr_err_ratelimited("%s: Request entry mismatch: 0x%llx <-> 0x%llx\n", __func__, (__u64)entry, (__u64)mind_req->entry);
		return 0;
	}

	// Note: without lock, entry should be read-only, except atomic counter for pending operations
	struct mind_io_request *mind_io_req = &entry->operations[task->op_index];
	switch (task->type)
	{
	case FAULT_ONLY:
		// read operation
#ifndef MIND_SKIP_KERNEL_BACKUP
#ifdef MIND_CHECK_DATA_CORRUPTION
		if (!ret && memcmp(mind_io_req->buf, entry->blkram->data + mind_io_req->pos, mind_io_req->len) != 0)
		{
			unsigned long diff_loc = 0;
			for (diff_loc = 0; diff_loc < mind_io_req->len; diff_loc++)
			{
				if (*((__u8 *)mind_io_req->buf + diff_loc) != *(entry->blkram->data + mind_io_req->pos + diff_loc))
				{
					break;
				}
			}
			// The data in the buffer is not the same as the data in blkram->data + pos
			pr_err_ratelimited("Data mismatch at position %lld, loc: %lu || 0x%lx <-> 0x%lx || off: %lu\n",
				mind_io_req->pos, diff_loc, *(unsigned long*)((__u8 *)mind_io_req->buf + diff_loc),
				*(unsigned long*)((entry->blkram->data + mind_io_req->pos + diff_loc)),
				(unsigned long)task->offset_to_data);
		}
#endif
		memcpy(mind_io_req->buf, entry->blkram->data + mind_io_req->pos, mind_io_req->len);
#endif
		break;
	case EVICTION_NEEDED:
#ifndef MIND_SKIP_KERNEL_BACKUP
		memcpy(entry->blkram->data + mind_io_req->pos, mind_io_req->buf, mind_io_req->len);
#endif
		break;
	default:
		pr_err_ratelimited("%s: Invalid task type: %d\n", __func__, task->type);
		break;
	}
	finish_mind_req(mind_req);
	return 1;
}

static blk_status_t serve_request(struct request_map_entry *entry)
{
	int ret = BLK_STS_IOERR;
	unsigned int idx = 0;

	if (!entry || !entry->rq || !entry->blkram)
	{
		pr_err_ratelimited("%s: Invalid request\n", __func__);
		ret = BLK_STS_IOERR;
		goto chk_err_and_return;
	}

	int req_to_send = atomic_read(&entry->num_pending);
	while (idx < req_to_send)
	{
		// check the in-flight messages
		if (atomic_read(&num_pending_rdma) >= actual_queue_size)	//max(1, actual_queue_size - 4))
		{
			// pr_info_ratelimited("%s: Pending requests are full: %u\n", __func__, atomic_read(&num_pending_pages));
			wait_with_sleep();
			continue;
		}
		struct mind_io_request *mind_io_req = &entry->operations[idx];
		if (mind_io_req->status == REQ_STATUS_IDLE)	// end of the operation list
		{
			break;
		}
		struct fault_task *task = NULL;
		struct mind_rdma_req *mind_req = NULL;
		switch (entry->opcode) {
		case REQ_OP_READ:
			task = (struct fault_task *)kzalloc(sizeof(*task), GFP_KERNEL);
			task->req = (__u64)entry->rq;
			task->fault_va = mind_io_req->pos;
			task->processed = 0;
			task->type = FAULT_ONLY;
			task->pfn = vmalloc_to_pfn(mind_io_req->buf);
			task->offset_to_data = 0;
			task->size = mind_io_req->len;
			task->op_index = idx;
			mind_req = mind_rdma_read(entry, (__u64)task, mind_io_req->buf, mind_io_req->pos, mind_io_req->len);
			if (!mind_req && working_status == WORKING)
			{
				pr_err_ratelimited("Cannot read data: position %lld\n", mind_io_req->pos);
			}
			ret = BLK_STS_OK;	// 0
			atomic_inc(&num_pending_pages);
			break;
		case REQ_OP_WRITE:
			task = (struct fault_task *)kzalloc(sizeof(*task), GFP_KERNEL);
			task->req = (__u64)entry->rq;
			task->fault_va = mind_io_req->pos;
			task->processed = 0;
			task->type = EVICTION_NEEDED;
			task->pfn = vmalloc_to_pfn(mind_io_req->buf);
			task->offset_to_data = 0;
			task->size = mind_io_req->len;
			task->op_index = idx;
			mind_req = mind_rdma_write(entry, (__u64)task, mind_io_req->buf, mind_io_req->pos, mind_io_req->len);
			if (!mind_req && working_status == WORKING)
			{
				pr_err_ratelimited("Cannot write data: position %lld\n", mind_io_req->pos);
			}
			ret = BLK_STS_OK;	// 0
			atomic_inc(&num_pending_pages);
			break;
		default:
			ret = BLK_STS_IOERR;
			break;
		}
		idx ++;
	}
	if (idx != req_to_send)
	{
		pr_err_ratelimited("%s: The number of requests mismatch: %u <-> %u\n",
			__func__, req_to_send, idx);
	}

chk_err_and_return:
	if (ret)
	{
		blk_mq_end_request(entry->rq, ret);
	}
	return ret;
}

kfifo_t_mind_io *get_mind_io_request_queue(void)
{
	return &mind_io_request_queue;
}

void initialize_worker_ctx(void)
{
    INIT_KFIFO(mind_io_request_queue);
}

int req_worker_func(void *data)
{
	struct request_map_entry *entry;

	pr_info("MIND block device :: Request worker thread started - %s\n", __func__);
    while (!kthread_should_stop() && working_status == WORKING) {
		unsigned int cnt = 0;
        while (1) {
			int is_get_data = 0;
			spin_lock(&entry_hashmap_lock);
			is_get_data = kfifo_get(&mind_io_request_queue, &entry);
			spin_unlock(&entry_hashmap_lock);
			if (!is_get_data)	// empty
			{
				break;
			}
			// Add request to the kernel-to-user queue
            serve_request(entry);
			// now, entry should be freed
			cnt ++;
			if (cnt > RETRY_WITHOUT_SLEEP)
			{
				wait_with_sleep();
				cnt = 0;
			}
        }
        // schedule();
		wait_with_sleep();
    }
	pr_info("MIND block device :: Request worker thread stopped\n");
    return 0;
}

struct mind_page_stats {
	__u64 unique_pages;
	__u64 average_refetch;
};

struct mind_page_stats collect_page_stats(void) {
#ifdef MIND_PAGE_STATS
	__u64 unique_pages = 0;
	__u64 average_refetch = 0;
	struct page_stat_entry *page_stat = NULL;
	int bkt;  // bucket iterator
	struct hlist_node *tmp;

	spin_lock(&page_stat_lock);
	hash_for_each_safe(served_pages_map, bkt, tmp, page_stat, node) {
		unique_pages++;
		average_refetch += page_stat->count;
#ifdef MIND_PAGE_STATS_RESET
		hash_del(&page_stat->node);
		kfree(page_stat);
#endif
	}
	spin_unlock(&page_stat_lock);
	struct mind_page_stats stats = {
		.unique_pages = unique_pages,
		.average_refetch = unique_pages > 0 ? average_refetch / unique_pages : 0
	};
	return stats;
#else
	struct mind_page_stats stats = {
		.unique_pages = 0,
		.average_refetch = 0
	};
	return stats;
#endif
}

int perf_print(void *_dummy)
{
	while (!kthread_should_stop() && working_status == WORKING) {
		// Check acks from the user space
		ssleep(1);
		// print the number of served pages
		int served_pages = atomic_read(&num_served_pages);
		int served_read_pages = atomic_read(&num_served_read_pages);
		// counters can be inaccurate
		atomic_set(&num_served_pages, 0);
		atomic_set(&num_served_read_pages, 0);
		// collect page stats
		struct mind_page_stats stats = collect_page_stats();
		unsigned long served_mbps = (unsigned long)served_pages * PAGE_SIZE * 8 / 1024 / 1024;
		unsigned long read_mbps = (unsigned long)served_read_pages * PAGE_SIZE * 8 / 1024 / 1024;
		pr_info("MIND block device :: Served pages: %u pages (uniq: %llu/%u, re-fet: %llu), %lu Mbps (r: %lu Mbps) :: pending %u pages (rdma %u)\n",
			served_pages, stats.unique_pages, served_read_pages, stats.average_refetch, served_mbps, read_mbps,
			atomic_read(&num_pending_pages), atomic_read(&num_pending_rdma));
	}

	return 0;
}

int ack_worker_func(void *_dummy)
{
#ifdef MIND_LOCAL_ONLY
	// sleep forever
	while (!kthread_should_stop() && working_status == WORKING) {
		// Check acks from the user space
		ssleep(1);
	}
#else
	// start server_request in a sepearate thread
	struct task_struct *perf_print_thread = kthread_run(perf_print, NULL, "mind_perf_printer");
	pr_info("MIND block device :: Ack-serving worker thread started - %s\n", __func__);
    while (!kthread_should_stop() && working_status == WORKING) {
		// Check acks from the user space
		if (serve_acks() < 0)
		{
			// if queue is empty, yield
			// schedule();
			wait_with_sleep();
		}
	}

	// sleep for a while
	pr_info("MIND block device :: Ack-serving worker terminating: start flushing remaining Acks\n");
	ssleep(3);
	while(serve_acks() >= 0)
	{
		// we are flushing any remaining acks
	}

	pr_info("MIND block device :: Ack-serving worker thread stopped\n");
#endif
	return 0;
}
