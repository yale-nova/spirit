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
#include "mind_ram_drv.h"
#include <linux/kfifo.h>
#include <linux/slab.h>
#include <linux/kthread.h>
#include <linux/in.h>
#include <linux/inet.h>
#include <linux/socket.h>
#include <rdma/ib_verbs.h>
#include <rdma/rdma_cm.h>

#define blk_dev_name "mind_ram"
#define WORKER_THREAD_CPU 1
#define ACK_WORKER_THREAD_CPU 2

struct dentry *config_dir = NULL;

// for worker thread
struct task_struct *worker_thread = NULL, *ack_worker_thread = NULL;
DEFINE_HASHTABLE(mind_request_map, MIND_REQ_HASH_BUCKET_SHIFT);	// 2^10 buckets
DECLARE_HASHTABLE(served_pages_map, MIND_PAGE_STAT_BUKCET_SHIFT);

// Block device configuration params
unsigned long capacity_mb = 40;
module_param(capacity_mb, ulong, 0644);
MODULE_PARM_DESC(capacity_mb, "capacity of the block device in MB");
EXPORT_SYMBOL_GPL(capacity_mb);

// number of segment per request
unsigned long max_segments = MIND_OP_PER_RQ;
module_param(max_segments, ulong, 0644);
MODULE_PARM_DESC(max_segments, "maximum segments");
EXPORT_SYMBOL_GPL(max_segments);

// maximum size of each segment per request
unsigned long max_segment_size = (256 * 1024);
module_param(max_segment_size, ulong, 0644);
MODULE_PARM_DESC(max_segment_size, "maximum segment size");
EXPORT_SYMBOL_GPL(max_segment_size);

int working_status = STOPPED;
module_param(working_status, int, 0600);
MODULE_PARM_DESC(working_status, "integer-encoded working status");

char* server_ip = "127.0.0.1";
module_param(server_ip, charp, 0644);
MODULE_PARM_DESC(server_ip, "IP address of the server to connect");

char* server_port = "50001";
module_param(server_port, charp, 0644);
MODULE_PARM_DESC(server_port, "Port of the server to connect");

char* rdma_device_name = NULL;
module_param(rdma_device_name, charp, 0644);
MODULE_PARM_DESC(rdma_device_name, "Name of the RDMA device to use (e.g., mlx5_0, mlx4_0). If not specified, uses the first suitable device.");

const unsigned long max_queue_length = 2 * MIND_QUEUE_SIZE_MAX;	// read and write - use max for sizing

unsigned long lbs = PAGE_SIZE;
unsigned long pbs = PAGE_SIZE;

// spinlock to project task to user
DEFINE_SPINLOCK(task_to_user_lock);
DEFINE_SPINLOCK(task_to_user_buffer_lock);
DEFINE_SPINLOCK(task_from_user_lock);
DEFINE_SPINLOCK(task_from_user_buffer_lock);
DEFINE_SPINLOCK(entry_hashmap_lock);
DEFINE_SPINLOCK(page_stat_lock);


// User mapping device related structs
static int mindram_user_mmap_to_user(struct file *filp, struct vm_area_struct *vma);
static int mindram_user_mmap_from_user(struct file *filp, struct vm_area_struct *vma);

static const struct file_operations mindram_to_user_fops = {
    .owner = THIS_MODULE,
    .mmap = mindram_user_mmap_to_user,
};

static const struct file_operations mindram_from_user_fops = {
    .owner = THIS_MODULE,
    .mmap = mindram_user_mmap_from_user,
};

static struct miscdevice mindram_to_user_dev = {
    .minor = MISC_DYNAMIC_MINOR,
    .name = MIND_FAULT_BUF_NAME_TO_USER,
    .fops = &mindram_to_user_fops,
};

static struct miscdevice mindram_from_user_dev = {
    .minor = MISC_DYNAMIC_MINOR,
    .name = MIND_FAULT_BUF_NAME_FROM_USER,
    .fops = &mindram_from_user_fops,
};

struct mind_rdma_queue *rdma_queue = NULL;
struct mind_fault_struct *fault_to_user = NULL, *fault_from_user = NULL;

static int major;
static DEFINE_IDA(blk_ram_indexes);
static struct blk_ram_dev_t *blk_ram_dev = NULL;

static int mindram_user_mmap(struct mind_fault_struct *f_struct, struct file *filp, struct vm_area_struct *vma)
{
	unsigned long physical = virt_to_phys((void *)f_struct);
	unsigned long vsize = vma->vm_end - vma->vm_start;
	unsigned long psize = PAGE_ALIGN(sizeof(struct mind_fault_struct));

	if (vsize > psize)
	{
		// print vsize, psize, other information
		pr_err_ratelimited("%s: vsize: %lu, psize: %lu, pa: 0x%lx\n", __func__, vsize, psize, physical);
		return -EINVAL;  // the application tried to mmap too much memory
	}

	if (remap_pfn_range(vma, vma->vm_start, physical >> PAGE_SHIFT, vsize, vma->vm_page_prot))
		return -EAGAIN;

	return 0;
}

static int mindram_user_mmap_to_user(struct file *filp, struct vm_area_struct *vma)
{
	return mindram_user_mmap(fault_to_user, filp, vma);
}

static int mindram_user_mmap_from_user(struct file *filp, struct vm_area_struct *vma)
{
	return mindram_user_mmap(fault_from_user, filp, vma);
}

static int init_user_queue(struct mind_fault_struct *f_struct)
{
	if (!f_struct)	// maybe redundant but keep it here for now
	{
		pr_err("Cannot initialize user queue, f_struct is NULL\n");
		return -EINVAL;
	}
	f_struct->version = MIND_FAULT_STRUCT_VERSION;

	f_struct->queue.head = 0;
	f_struct->queue.tail = 0;
	return 0;
}

static void release_to_user_dev(void)
{
	// release queue to user as well
	if (fault_to_user)
	{
		misc_deregister(&mindram_to_user_dev);
		// vfree(fault_to_user);
		kfree(fault_to_user);
		fault_to_user = NULL;
	}
}

static void release_from_user_dev(void)
{
	// release queue from user as well
	if (fault_from_user)
	{
		misc_deregister(&mindram_from_user_dev);
		// vfree(fault_from_user);
		kfree(fault_from_user);
		fault_from_user = NULL;
	}
}

static int mindram_user_init(void)
{
	int ret = 0;

	working_status = WORKING;	// start working
	spin_lock_init(&task_to_user_lock);
	spin_lock_init(&task_to_user_buffer_lock);
	spin_lock_init(&task_from_user_lock);
	spin_lock_init(&task_from_user_buffer_lock);
	spin_lock_init(&entry_hashmap_lock);
	spin_lock_init(&page_stat_lock);
	return 0;
}

static int mindram_user_release(void)
{
	return 0;
}

extern struct ib_client mind_rdma_ib_client;
static int mindram_rdma_init(void)
{
	int ret = 0;
	rdma_queue = kzalloc(sizeof(*rdma_queue), GFP_KERNEL);
	if (!rdma_queue)
	{
		pr_err("Failed to allocate memory for rdma_queue\n");
		return -ENOMEM;
	}
	// setup params
	rdma_queue->server_ip = server_ip;
	rdma_queue->server_port = server_port;
	rdma_queue->max_req_size_pages = max_segment_size / PAGE_SIZE;
	rdma_queue->server_mem_size = capacity_mb << 20;
	rdma_queue->status = STATUS_IDLE;
	init_completion(&rdma_queue->init_done);

	ret = ib_register_client(&mind_rdma_ib_client);
	if (ret)
	{
		pr_err("failed to register IB client: %d\n", ret);
		return ret;
	}

	// Log device selection preference
	if (rdma_device_name) {
		pr_info("Waiting for RDMA device: %s\n", rdma_device_name);
	} else {
		pr_info("Waiting for any suitable RDMA device\n");
	}

	ret = wait_for_completion_interruptible(&rdma_queue->init_done);
	if (ret) {
		pr_err("RDMA initialization failed or interrupted (%d)\n", ret);
		if (rdma_device_name) {
			pr_err("Failed to initialize specified RDMA device: %s\n", rdma_device_name);
		}
		ib_unregister_client(&mind_rdma_ib_client);
		return ret;
	}
out_rdma_create:
	return 0;
}

void mindram_rdma_release(void)
{
	pr_info("mindram_rdma_release\n");
	ib_unregister_client(&mind_rdma_ib_client);	// also queue should be removed in there
	pr_info("mind_rdma_ib_client unregistered\n");
}

// Block device operations
static blk_status_t blk_ram_queue_rq(struct blk_mq_hw_ctx *hctx,
				     const struct blk_mq_queue_data *bd)
{
	struct request *rq = bd->rq;
	blk_status_t err = BLK_STS_OK;
	struct bio_vec bv;
	struct req_iterator iter;
	loff_t pos = blk_rq_pos(rq) << SECTOR_SHIFT;
	struct blk_ram_dev_t *blkram = hctx->queue->queuedata;
	loff_t data_len = (blkram->capacity << SECTOR_SHIFT);
	struct request_map_entry *entry = kzalloc(sizeof(*entry), GFP_KERNEL);
	size_t idx = 0;

	if (!entry) {
		return BLK_STS_IOERR;
	}

	blk_mq_start_request(rq);
	entry->rq = rq;
	entry->blkram = blkram;
	entry->opcode = req_op(rq);

	// We need to manage per request structure to make sure that blk_mq_end_request and blk_mq_start_request called only once
	// Maybe we should mind_io_request per request, not page: check `struct request_map_entry` above
	// We can then push the pointer of the struct request_map_entry to the kfifo
	rq_for_each_segment(bv, rq, iter) {
		if (idx >= MIND_OP_PER_RQ) {
			err = BLK_STS_IOERR;
			pr_err_ratelimited("blk_ram_queue_rq: too many segments: %lu\n", idx);
			goto chk_err_and_return;
		}

		unsigned int len = bv.bv_len;
		void *buf = page_address(bv.bv_page) + bv.bv_offset;
		if (pos + len > data_len) {
			err = BLK_STS_IOERR;
			goto chk_err_and_return;
		}
		// TODO: allocate memory if needed (instead of copying the data)
		struct mind_io_request *req = &entry->operations[idx];
		req->buf = buf;
		req->pos = pos;
		req->len = len;
		req->status = REQ_STATUS_STARTED;
		pos += len;
		idx ++;

		if (len > PAGE_SIZE) {
			pr_err_ratelimited("blk_ram_queue_rq: len (%u) > PAGE_SIZE\n", len);
		}
	}

#ifdef MIND_DEBUG_ON
	if (idx > 16)
	{
		pr_info_ratelimited("blk_ram_queue_rq: %lu segments\n", idx);
	}
#endif
	atomic_set(&entry->num_pending, idx);

	spin_lock(&entry_hashmap_lock);
	hash_add(mind_request_map, &entry->node, (__u64)rq);
	smp_wmb();	// at the point it is visible inside the queue, it should be also visible in the hashmap
	if (!kfifo_put(get_mind_io_request_queue(), entry)) {
		// Handle the case where the kfifo is full
		printk(KERN_WARNING "mind_io_request_queue is full\n");
		err = BLK_STS_RESOURCE;
	}
	spin_unlock(&entry_hashmap_lock);
chk_err_and_return:
	// complete the request for error cases
	if (err != BLK_STS_OK)
	{
		kfree(entry);
		blk_mq_end_request(rq, err);
	}
	return err;
}

static const struct blk_mq_ops blk_ram_mq_ops = {
	.queue_rq = blk_ram_queue_rq,
	// .poll = blk_ram_poll,
};

static const struct block_device_operations blk_ram_rq_ops = {
	.owner = THIS_MODULE,
};

static int blk_device_init(void)
{
	int ret = 0;
	int minor;
	struct queue_limits lim = { };
	struct gendisk *disk;
	loff_t data_size_bytes = capacity_mb << 20;

	ret = register_blkdev(0, blk_dev_name);
	if (ret < 0)
		return ret;

	major = ret;

	blk_ram_dev = kzalloc(sizeof(struct blk_ram_dev_t), GFP_KERNEL);

	if (blk_ram_dev == NULL) {
		pr_err("memory allocation failed for blk_ram_dev\n");
		ret = -ENOMEM;
		goto unregister_blkdev;
	}

	blk_ram_dev->capacity = data_size_bytes >> SECTOR_SHIFT;
#ifndef MIND_SKIP_KERNEL_BACKUP
	blk_ram_dev->data = vzalloc(data_size_bytes);
	if (blk_ram_dev->data == NULL) {
		pr_err("memory allocation failed for the RAM disk\n");
		ret = -ENOMEM;
		goto data_err;
	}
#endif
	memset(&blk_ram_dev->tag_set, 0, sizeof(blk_ram_dev->tag_set));
	blk_ram_dev->tag_set.ops = &blk_ram_mq_ops;
	blk_ram_dev->tag_set.queue_depth = max_queue_length;
	blk_ram_dev->tag_set.numa_node = NUMA_NO_NODE;
	blk_ram_dev->tag_set.flags = BLK_MQ_F_SHOULD_MERGE | BLK_MQ_F_BLOCKING | BLK_MQ_F_TAG_HCTX_SHARED;
	blk_ram_dev->tag_set.cmd_size = 0;
	blk_ram_dev->tag_set.driver_data = blk_ram_dev;
	blk_ram_dev->tag_set.nr_hw_queues = 1;

	ret = blk_mq_alloc_tag_set(&blk_ram_dev->tag_set);
	if (ret)
		goto data_err;

	// New API for setting queue limits
	lim.logical_block_size = lbs;
	lim.physical_block_size = pbs;
	lim.max_segments = max_segments;
	lim.max_segment_size = max_segment_size;\
	lim.io_min = 64 * PAGE_SIZE;
	lim.io_opt = (1 << 24);
	// lim.io_opt = (pbs * max_segments);

	disk = blk_ram_dev->disk =
		blk_mq_alloc_disk(&blk_ram_dev->tag_set, &lim, blk_ram_dev);

	if (IS_ERR(disk)) {
		ret = PTR_ERR(disk);
		pr_err("Error allocating a disk\n");
		goto tagset_err;
	}

	// This is not necessary as we don't support partitions, and creating
	// more RAM backed devices with the existing module
	minor = ret = ida_alloc(&blk_ram_indexes, GFP_KERNEL);
	if (ret < 0)
		goto cleanup_disk;

	disk->major = major;
	disk->first_minor = minor;
	disk->minors = 1;
	snprintf(disk->disk_name, DISK_NAME_LEN, "mind_ram%d", minor);
	disk->fops = &blk_ram_rq_ops;
	disk->flags = GENHD_FL_NO_PART;
	set_capacity(disk, blk_ram_dev->capacity);

	ret = add_disk(disk);
	if (ret < 0)
		goto cleanup_disk;

	pr_info("mind_ram block module has been loaded successfully\n");
	return 0;

cleanup_disk:
	put_disk(blk_ram_dev->disk);
tagset_err:
#ifndef MIND_SKIP_KERNEL_BACKUP
	vfree(blk_ram_dev->data);
#endif
data_err:
	kfree(blk_ram_dev);
unregister_blkdev:
	unregister_blkdev(major, blk_dev_name);
	return ret;
}

static int blk_device_release(void)
{
	if (blk_ram_dev->disk) {
		del_gendisk(blk_ram_dev->disk);
		put_disk(blk_ram_dev->disk);
	}
#ifndef MIND_SKIP_KERNEL_BACKUP
	vfree(blk_ram_dev->data);
#endif
	kfree(blk_ram_dev);
	unregister_blkdev(major, blk_dev_name);
	return 0;
}

static int debugfs_init(void)
{
	config_dir = debugfs_create_dir(blk_dev_name, NULL);
	if (config_dir == NULL)
		return -ENOMEM;

	debugfs_create_ulong("capacity_mb", 0400, config_dir, &capacity_mb);
	debugfs_create_x32("working_status", 0600, config_dir, &working_status);

	// Add RDMA device name to debugfs for runtime inspection
	if (rdma_device_name) {
		debugfs_create_str("rdma_device_name", 0400, config_dir, &rdma_device_name);
	}

	return 0;
}

static int debugfs_release(void)
{
	if (config_dir)
		debugfs_remove_recursive(config_dir);
	return 0;
}

// General module initialization/release functions
// - We need to populate one block device for the kernel to set up swap and
//   two misc devices for user to map to kernel (fault queues)
static int __init blk_ram_init(void)
{
	int ret = 0;
	working_status = STOPPED;	// shutting down

	ret = mindram_rdma_init();
	if (ret)
	{
		goto err_rdma_init;
	}

	ret = blk_device_init();
	if (ret)
	{
		goto err_device_init;
	}

	ret = mindram_user_init();
	if (ret)
	{
		goto err_user_init;
	}

	ret = debugfs_init();
	if (ret)
	{
		goto err_debugfs;
	}

	// worker and FIFO
	initialize_worker_ctx();
	unsigned int num_cpus = num_online_cpus();
	if (num_cpus < 3) {
		pr_warn("Less than 2 CPUs available, skipping thread binding\n");
	}
	ack_worker_thread = kthread_create(ack_worker_func, NULL, "mind_blk_ack_worker");
	if (IS_ERR(ack_worker_thread)) {
		printk(KERN_ERR "Failed to create ack worker thread\n");
		ret = PTR_ERR(ack_worker_thread);
		goto err_ack_worker;
	}
	kthread_bind(ack_worker_thread, WORKER_THREAD_CPU);
	wake_up_process(ack_worker_thread);

	// Create and bind req worker to last core
	worker_thread = kthread_create(req_worker_func, NULL, "mind_blk_req_worker");
	if (IS_ERR(worker_thread)) {
		printk(KERN_ERR "Failed to create request worker thread\n");
		ret = PTR_ERR(worker_thread);
		goto err_req_worker;
	}
	kthread_bind(worker_thread, ACK_WORKER_THREAD_CPU);
	wake_up_process(worker_thread);

	pr_info("module initialized\n");
	return 0;	// success
err_req_worker:
	working_status = STOPPED;
	kthread_stop(ack_worker_thread);
err_ack_worker:
	debugfs_release();
err_debugfs:
	mindram_user_release();
err_user_init:
	blk_device_release();
err_device_init:
	mindram_rdma_release();
err_rdma_init:
	return ret;
}

static void __exit blk_ram_exit(void)
{
	working_status = STOPPED;	// shutting down
	kthread_stop(worker_thread);
	kthread_stop(ack_worker_thread);
	debugfs_release();
	mindram_user_release();
	blk_device_release();
	mindram_rdma_release();
	pr_info("module unloaded\n");
}

module_init(blk_ram_init);
module_exit(blk_ram_exit);

MODULE_AUTHOR("MIND");
MODULE_LICENSE("GPL");
