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

#include "mind_ram_drv.h"
#include <linux/slab.h>
#include <linux/kthread.h>

// Define a kfifo for struct mind_io_request
kfifo_t_mind_io mind_io_request_queue;
extern int working_status;
extern spinlock_t task_to_user_lock;
extern spinlock_t task_from_user_lock;
extern spinlock_t task_to_user_buffer_lock;
extern spinlock_t task_from_user_buffer_lock;
extern spinlock_t entry_hashmap_lock;
extern struct mind_fault_struct *fault_to_user, *fault_from_user;
extern DECLARE_HASHTABLE(mind_request_map, MIND_REQ_HASH_BUCKET_SHIFT);
static atomic_t num_pending_reqs = ATOMIC_INIT(0);
static atomic_t num_served_pages = ATOMIC_INIT(0);

static void wait_with_sleep(void)
{
	// udelay(WAIT_RESPONSE_TIME_IN_US);
	usleep_range(WAIT_RESPONSE_TIME_IN_US, WAIT_RESPONSE_TIME_IN_US);
}

// User mapping interface operations
static struct fault_queue *get_queue_from_user(void)
{
	if (fault_from_user == NULL) {
		return NULL;
	}
	return &fault_from_user->queue;
}

static struct fault_queue *get_queue_to_user(void)
{
	if (fault_to_user == NULL) {
		return NULL;
	}
	return &fault_to_user->queue;
}

static struct fault_buffer *get_buffer_from_user(void)
{
	if (fault_from_user == NULL) {
		return NULL;
	}
	return &fault_from_user->buffer;
}

static struct fault_buffer *get_buffer_to_user(void)
{
	if (fault_to_user == NULL) {
		return NULL;
	}
	return &fault_to_user->buffer;
}

// Copy (or ship) data from kernel to user
// @return: the offset to the used buffer location; -1 in unsigned long if failed
static unsigned long copy_data_to_user(char* data_ptr, unsigned long size)
{
#ifdef MIND_DEBUG_ON
	int retry_cnt = DEBUG_RETRY_CNT;
#endif
	while(working_status == WORKING)
	{
		unsigned long offset;

		// check retry_cnt
#ifdef MIND_DEBUG_ON
		retry_cnt--;
		if (retry_cnt == 0)
		{
			pr_err_ratelimited("%s: Cannot copy data to user\n", __func__);
			// working_status = -2;
			retry_cnt = DEBUG_RETRY_CNT;
		}
#endif
		// Try to copy data to buffer
		offset = copy_data_to_buffer(get_buffer_to_user(), data_ptr, size);
		if (offset == -1)
		{
			pr_err_ratelimited("%s: Buffer is full, cannot copy data to user\n", __func__);
			// wait_with_sleep();
			continue;
		}
		return offset;
	}
	return -1;
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

static int mind_ram_read(__u64 req, unsigned int idx, void *buf, unsigned long addr, unsigned long len)
{
	// unsigned int for idx should be more than sufficient (idx of operation within a request; MIND_OP_PER_RQ)
	struct fault_task task;
	int ret = 0;

	if (working_status != WORKING)
	{
		return -1;
	}

	task.req = req;
	task.fault_va = addr;
	task.processed = 0;
	task.type = FAULT_ONLY;
	task.pfn = vmalloc_to_pfn(buf);
	task.offset_to_data = 0;	// data will be from the user, so 0 here
	task.size = len;
	task.op_index = idx;

	ret = -1;
	while (ret && working_status == WORKING)
	{
		spin_lock(&task_to_user_lock);
		ret = push_task(get_queue_to_user(), &task);
		spin_unlock(&task_to_user_lock);
		if (ret)
		{
#ifdef MIND_DEBUG_ON
			pr_err_ratelimited(
				"%s: Cannot copy data to user | addr: 0x%lx, len: 0x%lx\n",
				__func__, addr, len);
			return -1;
#endif
			wait_with_sleep();
		}
	}
	return ret;
}

static int mind_ram_read_local(struct request_map_entry *entry, void *buf, unsigned long addr, unsigned long len)
{
	// unsigned int for idx should be more than sufficient (idx of operation within a request; MIND_OP_PER_RQ)
	struct fault_task task;
	int ret = BLK_STS_OK;
#ifdef MIND_LOCAL_ONLY
	// Ref) mind_ram_read((__u64)entry->rq, idx, mind_req->buf, mind_req->pos, mind_req->len);
	memcpy(buf, entry->blkram->data + addr, len);
#endif
	return ret;
}

// XXX: buffer copy and task enqueing must be a single atomic (not thread safe)
static int mind_ram_write(__u64 req, unsigned int idx, void *buf, unsigned long addr, unsigned long len)
{
	struct fault_task task;
	int ret = 0;

	if (working_status != WORKING)
	{
		return -1;
	}

	task.req = req;
	task.fault_va = addr;
	task.processed = 0;
	task.type = EVICTION_NEEDED;
	task.pfn = vmalloc_to_pfn(buf);
	task.size = len;
	task.op_index = idx;
	spin_lock(&task_to_user_buffer_lock);
	task.offset_to_data = copy_data_to_user(buf, len);
#ifdef MIND_PRINT_QUEUE
	// debugging header: print the read data in HEX
	if (!addr)
	{
		pr_info("Offset: %llu\n", task.offset_to_data);
		debug_mind_print_buf_hex(&get_buffer_to_user()->data_buf[task.offset_to_data], len, 0);
	}
#endif
	spin_unlock(&task_to_user_buffer_lock);

	if (task.offset_to_data == -1) {
		pr_err_ratelimited(
			"%s: Cannot copy data to user | addr: 0x%lx, len: 0x%lx\n",
			__func__, addr, len);
		return -1;
	}

	ret = -1;
	while (ret && working_status == WORKING) {
		spin_lock(&task_to_user_lock);
		ret = push_task(get_queue_to_user(), &task);
		spin_unlock(&task_to_user_lock);
		if (ret) {
			pr_err_ratelimited(
				"%s: Queue is full, cannot push task | addr: 0x%lx, len: 0x%lx\n",
				__func__, addr, len);
			// return -1;
			wait_with_sleep();
		}
	}
	return ret;
}

static int mind_ram_write_local(struct request_map_entry *entry, void *buf, unsigned long addr, unsigned long len)
{
	// unsigned int for idx should be more than sufficient (idx of operation within a request; MIND_OP_PER_RQ)
	struct fault_task task;
	int ret = BLK_STS_OK;
#ifdef MIND_LOCAL_ONLY
	// Ref) mind_ram_read((__u64)entry->rq, idx, mind_req->buf, mind_req->pos, mind_req->len);
	memcpy(entry->blkram->data + addr, buf, len);
#endif
	return ret;
}

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

// NOTE: user space must prioritize free up kernel -> user queue.
// Here, we opportunistically serve the requests (if there is any)
// @return: -1 if queue is empty, 0 if no task is served, 1 if a task is served
static int serve_acks(void)
{
	struct fault_task task;
	int ret = -1;
	struct request_map_entry *entry = NULL;
	// POLL until the task is processed
	memset(&task, 0, sizeof(struct fault_task));
	spin_lock(&task_from_user_lock);
	ret = pop_task(get_queue_from_user(), &task);
	spin_unlock(&task_from_user_lock);
	if (ret)
	{
		// pr_err_ratelimited("%s: Queue is empty, task has not been provided by the user space\n", __func__);
		return -1;
	}

	// Find the corresponding request and serve it
	entry = get_request_entry(task.req);
	if (!entry)
	{
		pr_err_ratelimited("%s: Cannot find the request entry\n", __func__);
		return 0;
	}
	// so, task = get_task_from_user();
	struct mind_io_request *mind_req = &entry->operations[task.op_index];
	// Note: without lock, entry should be read-only, except atomic counter for pending operations
	switch (task.type)
	{
	case FAULT_ONLY:
		// read operation
		spin_lock(&task_from_user_buffer_lock);
		copy_data_from_buffer(get_buffer_from_user(), task.offset_to_data, mind_req->buf, mind_req->len);
		spin_unlock(&task_from_user_buffer_lock);

#ifdef MIND_PRINT_QUEUE
		// debugging header: print the read data in HEX
		if (!addr)
		{
			debug_mind_print_buf_hex(mind_req->buf, mind_req->len, 1);
		}
#endif
#ifndef MIND_SKIP_KERNEL_BACKUP
#ifdef MIND_CHECK_DATA_CORRUPTION
		if (!ret && memcmp(mind_req->buf, entry->blkram->data + mind_req->pos, mind_req->len) != 0)
		{
			unsigned long diff_loc = 0;
			for (diff_loc = 0; diff_loc < mind_req->len; diff_loc++)
			{
				if (*((u8 *)mind_req->buf + diff_loc) != *(entry->blkram->data + mind_req->pos + diff_loc))
				{
					break;
				}
			}
			// The data in the buffer is not the same as the data in blkram->data + pos
			pr_err_ratelimited("Data mismatch at position %lld, loc: %lu || 0x%lx <-> 0x%lx || head: %lu, tail: %lu, off: %lu\n",
				mind_req->pos, diff_loc, *(unsigned long*)((u8 *)mind_req->buf + diff_loc),
				*(unsigned long*)((entry->blkram->data + mind_req->pos + diff_loc)),
				(unsigned long)get_buffer_from_user()->head, 
				(unsigned long)get_buffer_from_user()->tail,
				(unsigned long)task.offset_to_data);
		}
#endif
		memcpy(mind_req->buf, entry->blkram->data + mind_req->pos, mind_req->len);
#endif
		break;
	case EVICTION_NEEDED:
		// TODO: we can skip ACK from the user space for write operation (=no this switch case), 
		// since it should be processed earlier than later reads on the same data	
#ifndef MIND_SKIP_KERNEL_BACKUP
			memcpy(entry->blkram->data + mind_req->pos, mind_req->buf, mind_req->len);
#endif
		break;
	}

	if (entry->operations[task.op_index].status != REQ_STATUS_ACKED)
	{
		entry->operations[task.op_index].status = REQ_STATUS_ACKED;
		if (atomic_dec_return(&entry->num_pending))
		{
			// still pending operations
			return 1;
		}
	} else {
		pr_err_ratelimited("%s: The operation has been ACKed already\n", __func__);
		return 0;
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

	if (atomic_dec_return(&num_pending_reqs))
	{
		// pr_info_ratelimited("%s: There are still pending requests: %lu\n", __func__, (unsigned long)atomic_read(&num_pending_reqs));
		;
	}
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

	while (idx < MIND_OP_PER_RQ)
	{
		struct mind_io_request *mind_req = &entry->operations[idx];
		if (mind_req->status == REQ_STATUS_IDLE)	// end of the operation list
		{
			break;
		}
		switch (entry->opcode) {
		case REQ_OP_READ:
#ifdef MIND_LOCAL_ONLY
			ret = mind_ram_read_local(entry, mind_req->buf, mind_req->pos, mind_req->len);
			atomic_add(mind_req->len / PAGE_SIZE, &num_served_pages);
#else
			ret = mind_ram_read((__u64)entry->rq, idx, mind_req->buf, mind_req->pos, mind_req->len);
			if (ret && working_status == WORKING)
			{
				pr_err_ratelimited("Cannot read data to the daemon: position %lld\n", mind_req->pos);
			}
#endif
			break;
		case REQ_OP_WRITE:
#ifdef MIND_LOCAL_ONLY
			ret = mind_ram_write_local(entry, mind_req->buf, mind_req->pos, mind_req->len);
#else
			ret = mind_ram_write((__u64)entry->rq, idx, mind_req->buf, mind_req->pos, mind_req->len);
			if (ret && working_status == WORKING)
			{
				pr_err_ratelimited("Cannot write data to the daemon: position %lld\n", mind_req->pos);
			}
#endif
			break;
		default:
			ret = BLK_STS_IOERR;
			break;
		}
		idx ++;
		// DEBUG:: one at a time
		// while (serve_acks() <= 0)
		// {
		// 	// try until we have served the acks
		// }
		// atomic_inc(&num_served_pages);
		// atomic_add(mind_req->len / PAGE_SIZE, &num_served_pages);
	}
#ifdef MIND_LOCAL_ONLY
	// Should be servied here, not requiring ack handling
	blk_mq_end_request(entry->rq, BLK_STS_OK);
	hash_del(&entry->node);
	kfree(entry);
#else
	atomic_inc(&num_pending_reqs);
#endif

chk_err_and_return:
	if (ret)
	{
		blk_mq_end_request(entry->rq, ret);
	}

	// DEBUG:: all at once
	// while (idx > 0)
	// {
	// 	if (serve_acks() > 0)
	// 		idx --;
	// }
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
        schedule();
    }
	pr_info("MIND block device :: Request worker thread stopped\n");
    return 0;
}

int ack_worker_func(void *_dummy)
{
#ifdef MIND_LOCAL_ONLY
	// sleep forever
	while (!kthread_should_stop() && working_status == WORKING) {
		// Check acks from the user space
		ssleep(1);
		// print the number of served pages
		int served_pages = atomic_read(&num_served_pages);
		atomic_set(&num_served_pages, 0);	// it can be inaccurate
		unsigned long served_mbps = (unsigned long)served_pages * PAGE_SIZE * 8 / 1024 / 1024;
		pr_info("MIND block device :: Served pages: %u pages, %lu Mbps\n", served_pages, served_mbps);
	}
#else
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
