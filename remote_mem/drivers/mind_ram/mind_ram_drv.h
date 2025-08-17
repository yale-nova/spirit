#ifndef _MIND_RAM_DRV_H_
#define _MIND_RAM_DRV_H_

#define QUEUE_SIZE 896
#define BUFFER_SIZE (3584 * 1024) // 2 MB

typedef enum { FAULT_ONLY, EVICTION_NEEDED } fault_type_t;
#define MIND_FAULT_STRUCT_VERSION 1

#ifndef RETRY_WITHOUT_SLEEP
#define RETRY_WITHOUT_SLEEP 10000
#endif

#define MIND_QUEUE_SIZE_MAX 128	// maximum in-flight msgs - actual value determined by device capabilities

#ifdef __KERNEL__
// config flags
// #define MIND_DEBUG_ON
// #define MIND_PRINT_QUEUE
// #define MIND_PAGE_STATS
// #define MIND_PAGE_STATS_RESET
#define DEBUG_RETRY_CNT 10
#define WAIT_RESPONSE_TIME_IN_US 10
#define MIND_SKIP_KERNEL_BACKUP
// #define MIND_CHECK_DATA_CORRUPTION
// #define MIND_LOCAL_ONLY
#ifdef MIND_LOCAL_ONLY
#ifdef MIND_SKIP_KERNEL_BACKUP
#error "MIND_LOCAL_ONLY and MIND_SKIP_KERNEL_BACKUP cannot be used together"
#endif
#endif

// Block device related structs
struct blk_ram_dev_t {
	sector_t capacity;
#ifndef MIND_SKIP_KERNEL_BACKUP
	__u8 *data;
#endif
	struct blk_mq_tag_set tag_set;
	struct gendisk *disk;
};

// NOTE) this must be shared with the user space structure below
struct fault_task {
	__u64 req; // va of request structure as an identifier
	__u64 fault_va; // fault virtual address or offset from the beginning of the region
	__u32 processed;
	fault_type_t type;
	__u64 offset_to_data; // to data_buf
	__u64 pfn;
	__u64 size;
	__u32 op_index;
} __attribute__((packed));

struct fault_queue {
	struct fault_task buffer[QUEUE_SIZE];
	__u32 head;
	__u32 tail;
} __attribute__((packed));

struct fault_buffer {
	__u32 head;
	__u32 tail;
	char data_buf[BUFFER_SIZE];
} __attribute__((packed));

// NOTE: this struct must be 'allocated' not placed in the stack
struct mind_fault_struct {
	__u32 version; // any following structure should be based on this value
	// TODO: add flags that can be used to signal the kernel that the user is ready to receive data
	struct fault_queue queue;
	struct fault_buffer buffer;
} __attribute__((packed));

// Requests for read/write operations
enum req_status {
	REQ_STATUS_IDLE = 0,
	REQ_STATUS_STARTED,
	REQ_STATUS_PUSHED,
	REQ_STATUS_ACKED,
	REQ_STATUS_ERROR
};

struct mind_io_request {
	__u8 status;
	void *buf;
	loff_t pos;
	unsigned int len;
} __attribute__((packed));

// per-request map entry to track each read/write
#define MIND_OP_PER_RQ 256
#define MIND_REQ_HASH_BUCKET_SHIFT 10
#define MIND_PAGE_STAT_BUKCET_SHIFT 16	// 64K
#define MIND_POLL_RETRY_CNT 10000

#include <linux/types.h>
#include <linux/hashtable.h>
#include <linux/atomic.h>
struct request_map_entry {
	// TODO: add per request spin_lock
	struct request *rq;
	struct blk_ram_dev_t *blkram;
	struct mind_io_request operations[MIND_OP_PER_RQ];
	struct hlist_node node;
	atomic_t num_pending;
	enum req_op opcode;
};

struct page_stat_entry {
	__u64 va;
	__u64 count;
	struct hlist_node node;
};

// manual definition of kfifo
#include <linux/kfifo.h>
typedef STRUCT_KFIFO(struct request_map_entry *, 1024) kfifo_t_mind_io;

// working status
enum { WORKING = 0, STOPPED };

kfifo_t_mind_io *get_mind_io_request_queue(void);
void initialize_worker_ctx(void);
int req_worker_func(void *data);
int ack_worker_func(void *data);

// RDMA related kernel structs
struct mind_rdma_device {
	struct ib_device	*dev;
	struct ib_pd		*pd;
	// struct kref		ref;
};

enum status {
	STATUS_IDLE = 0,
	STATUS_QUEUE_CREATED,
};

struct mind_rdma_queue {
	struct mind_rdma_device	*dev;
	struct sockaddr_storage	server_addr;
	struct rdma_cm_id		*cm_id;
	struct completion		cm_done;
	int						cm_error;
	struct ib_cq			*cq;
	int						cq_size;
	struct ib_qp			*qp;
	int						max_req_size_pages;
	enum status				status;
	struct completion		init_done;
	// struct ib_mr			*mr;
	// address and port: alive until the module is removed
	char					*server_ip;
	char					*server_port;
	__u64					server_base_addr;
	__u64					server_mem_size;
	__u32					server_rkey;
};

#else
#include <stdint.h>

#ifndef min
#define min(a,b) \
({ __typeof__ (a) _a = (a); \
	__typeof__ (b) _b = (b); \
	_a < _b ? _a : _b; })
#endif

struct fault_task {
	uint64_t req;
	uint64_t fault_va; // fault virtual address or offset from the beginning of the region
	uint32_t processed;
	fault_type_t type;
	uint64_t offset_to_data; // to data_buf
	uint64_t pfn;
	uint64_t size;
	uint32_t op_index;
} __attribute__((packed));

struct fault_queue {
	struct fault_task buffer[QUEUE_SIZE];
	uint32_t head;
	uint32_t tail;
} __attribute__((packed));

struct fault_buffer {
	volatile uint32_t head;
	volatile uint32_t tail;
	char data_buf[BUFFER_SIZE];
} __attribute__((packed));

// NOTE: this struct must be 'allocated' not placed in the stack
struct mind_fault_struct {
	uint32_t version; // any following structure should be based on this value
	// TODO: add flags that can be used to signal the kernel that the user is ready to receive data
	struct fault_queue queue;
	struct fault_buffer buffer;
} __attribute__((packed));
#endif

#ifdef __KERNEL__
static_assert(sizeof(struct fault_task) == sizeof(uint64_t) * 5 +
						   sizeof(uint32_t) * 2 +
						   sizeof(fault_type_t),
	      "fault_task has different size in kernel space");
#else
#include <assert.h>
static_assert(sizeof(struct fault_task) == sizeof(uint64_t) * 5 +
						   sizeof(uint32_t) * 2 +
						   sizeof(fault_type_t),
	      "fault_task has different size in user space");
#endif

#define MIND_FAULT_BUF_NAME_TO_USER "mind_ram_to_user"
#define MIND_FAULT_BUF_NAME_FROM_USER "mind_ram_from_user"

// Base function that does not require write operations
static int is_queue_full(struct fault_queue *queue)
{
	return (queue->tail + 1) % QUEUE_SIZE == queue->head;
}

static int is_queue_empty(struct fault_queue *queue)
{
	return queue->head == queue->tail;
}

static void mem_barrier(void)
{
#ifdef __KERNEL__
	// smp_wmb(); // Memory barrier for kernel space
	smp_mb(); // Memory barrier for SMP safety
#else
	__sync_synchronize(); // Memory barrier for user space
#endif
}

static int push_task(struct fault_queue *queue, struct fault_task *task)
{
	if (is_queue_full(queue)) {
		return -1;
	}

	task->processed = 0;
	queue->buffer[queue->tail] = *task;
	mem_barrier();
	queue->tail = (queue->tail + 1) % QUEUE_SIZE;
	return 0;
}

// User dequeue entries from the head, and kernel enqueue entries to the tail
// @return: -1 if no task is processed, 0 if a task is popped
static int pop_task(struct fault_queue *queue, struct fault_task *task)
{
	if (!task) {
		return -1;
	}

	if (is_queue_empty(queue)) {
		return -1;
	}

	*task = queue->buffer[queue->head];
	// XXX: meaningless check since it is copied
	if (task->processed) {
#ifdef __KERNEL__
		pr_err_ratelimited(
			"ERROR: task has been already processed: 0x%lx\n",
			(unsigned long)task->fault_va);
#else
		printf("ERROR: task has been already processed: 0x%lx\n",
		       (unsigned long)task->fault_va);
#endif
		return -1;
	}
	task->processed = 0;
	mem_barrier();
	// Since we copied the fault_task, update the queue
	queue->head = (queue->head + 1) % QUEUE_SIZE;
	return 0;
}

static int is_buffer_full(struct fault_buffer *buffer, unsigned long size) {
    unsigned long available_space;

    if (buffer->head == buffer->tail) {
        // Buffer is empty, but need to leave an empty slot to distinguish from full buffer
        available_space = BUFFER_SIZE - 1;
    } else if (buffer->head > buffer->tail) {
        // Space from head to the end of the buffer plus space from the beginning of the buffer to tail
        available_space = (BUFFER_SIZE - buffer->head) + (buffer->tail - 1);
    } else { // buffer->head < buffer->tail
        // Space from tail to head, leaving one slot to differentiate full buffer
        available_space = (buffer->tail - buffer->head - 1);
    }

    return size >= available_space ? 1 : 0;
}

// ASSUMPTION: single producer, single consumer
static void copy_data_from_buffer(struct fault_buffer *src_buffer,
				  unsigned long offset, void *data_dst_ptr,
				  unsigned long size)
{
	if (size < BUFFER_SIZE - offset) {
		memcpy(data_dst_ptr, &src_buffer->data_buf[offset], size);
	} else {
		memcpy(data_dst_ptr, &src_buffer->data_buf[offset],
		       BUFFER_SIZE - offset);
		memcpy(data_dst_ptr + BUFFER_SIZE - offset,
		       &src_buffer->data_buf[0], size - (BUFFER_SIZE - offset));
	}
	mem_barrier();
#ifdef __KERNEL__
	if (offset != src_buffer->tail) {
		pr_err_ratelimited(
			"ERROR: try to copy data from non-tail location: %lu | tail: %u\n",
			offset, src_buffer->tail);
	}
#else
	if (offset != src_buffer->tail) {
		printf("ERROR: try to copy data from non-tail location: %lu | tail: %u\n",
		       offset, src_buffer->tail);
	}
#endif
    src_buffer->tail = (src_buffer->tail + size) % BUFFER_SIZE;
}

// ASSUMPTION: single producer, single consumer
// @return: offset to the buffer, -1 if failed
static unsigned long copy_data_to_buffer(struct fault_buffer *dst_buffer, void *data_dst_ptr, unsigned long size) {
	unsigned long offset = dst_buffer->head;
    unsigned long first_part_size = min(size, BUFFER_SIZE - offset);
    if (is_buffer_full(dst_buffer, size)) {
        return -1; // Buffer is full
    }
    memcpy(&dst_buffer->data_buf[offset], (char *)data_dst_ptr, first_part_size);
    if (size > first_part_size) { // Need to wrap around
        memcpy(&dst_buffer->data_buf[0], (char *)data_dst_ptr + first_part_size, size - first_part_size);
    }
	mem_barrier();
    dst_buffer->head = (dst_buffer->head + size) % BUFFER_SIZE;
    return offset;
}
#endif
