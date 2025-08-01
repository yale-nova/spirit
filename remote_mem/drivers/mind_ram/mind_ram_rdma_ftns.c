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
#include <linux/kfifo.h>
#include <linux/slab.h>
#include <linux/kthread.h>
#include <linux/in.h>
#include <linux/inet.h>
#include <linux/socket.h>
#include <rdma/ib_verbs.h>
#include <rdma/rdma_cm.h>
#include <rdma/mr_pool.h>
#include <linux/atomic.h>
#include <linux/errno.h>
#include <linux/minmax.h>
#include "mind_ram_drv.h"
#include "mind_ram_drv_rdma.h"

// module-specific global vars from mind_ram_drv_rdma.c
extern struct mind_rdma_queue *rdma_queue;
extern atomic_t num_pending_rdma;

// Actual queue size determined by device capabilities
int actual_queue_size = 16;  // Default fallback

static int mind_rdma_map_data(struct mind_rdma_req* mind_req, void *buf, unsigned long len, int writing)
{
    struct mind_rdma_queue *queue = rdma_queue;
    struct ib_device *ibdev = queue->dev->dev;
    int ret;

    if (!PAGE_ALIGNED(buf))
    {
        pr_err("requested addr is not page aligned: 0x%lx\n", (unsigned long)buf);
        return -EINVAL;
    }

    struct page *page = virt_to_page(buf);
    sg_set_page(&mind_req->sglist, page, len, 0);  // offset = 0, since the request is always page aligned
    // map sglist
    mind_req->dir = writing ? DMA_TO_DEVICE : DMA_FROM_DEVICE;
    ret = ib_dma_map_sg(ibdev, &mind_req->sglist, 1, mind_req->dir);
	if (ret <= 0)
	{
		pr_err("ib_dma_map_sg failed (%d)\n", ret);
		return ret;
	}

	return 0;
}

void unmap_mind_req(struct mind_rdma_req *mind_req)
{
    struct mind_rdma_queue *queue = rdma_queue;
    struct ib_device *ibdev = queue->dev->dev;

	// unmap rm and remove mind req
    ib_dma_unmap_sg(ibdev, &mind_req->sglist, 1, mind_req->dir);
    kfree(mind_req);
}

struct mind_rdma_req *mind_rdma_read(
	struct request_map_entry *entry, __u64 task_va, void *buf,
	unsigned long addr, unsigned long len)
	{
	struct mind_rdma_queue *queue = rdma_queue;
	struct mind_rdma_req *mind_req = kzalloc(sizeof(*mind_req), GFP_KERNEL);
	struct ib_rdma_wr *rdma_wr = &mind_req->rdma_wr;
	struct ib_send_wr *wr = &rdma_wr->wr;
	struct ib_sge sge;
	int ret;

	if (!queue || !mind_req)
		return NULL;

	// map data
	ret = mind_rdma_map_data(mind_req, buf, len, 0);    // no writing, so 0
	if (ret)
	{
		pr_err("mind_rdma_read::mind_rdma_map_data failed (%d)\n", ret);
		goto clear;
	}

	// prepare rdma wr
	mind_req->entry = entry;
	mind_req->task_va = task_va;
	memset(rdma_wr, 0, sizeof(*rdma_wr));
	rdma_wr->remote_addr = queue->server_base_addr + addr;
	rdma_wr->rkey = queue->server_rkey;
	// sge
	// TODO: match the lifespan of sge and wr
	sge.addr = mind_req->sglist.dma_address;	// (u64)buf;	//
	sge.length = len;	// mind_req->sglist.length;
	sge.lkey = queue->dev->pd->local_dma_lkey;	// mind_req->mr->lkey;

	// send wr
	memset(wr, 0, sizeof(*wr));
	wr->wr_id = (__u64)mind_req;
	wr->opcode = IB_WR_RDMA_READ;
	wr->sg_list = &sge;
	wr->num_sge = 1;
	wr->send_flags = IB_SEND_SIGNALED;

	ret = ib_post_send(queue->qp, wr, NULL);
	if (ret) {
		pr_err("mind_rdma_read::ib_post_send failed (%d)\n", ret);
		goto clear;
	}
	atomic_inc(&num_pending_rdma);
	return mind_req;
clear:
	kfree(mind_req);
	return NULL;
}

struct mind_rdma_req *mind_rdma_write(
	struct request_map_entry *entry, __u64 task_va, void *buf,
	unsigned long addr, unsigned long len)
{
	struct mind_rdma_queue *queue = rdma_queue;
	struct mind_rdma_req *mind_req = kzalloc(sizeof(*mind_req), GFP_KERNEL);
	struct ib_rdma_wr *rdma_wr = &mind_req->rdma_wr;
	struct ib_send_wr *wr = &rdma_wr->wr;
	struct ib_sge sge;
	int ret;

	if (!queue || !mind_req)
		return NULL;

	// map data
	ret = mind_rdma_map_data(mind_req, buf, len, 1);    // writing, so 1
	if (ret)
	{
		pr_err("mind_rdma_write::mind_rdma_map_data failed (%d)\n", ret);
		goto clear;
	}

	// prepare wr
	mind_req->entry = entry;
	mind_req->task_va = task_va;
	memset(rdma_wr, 0, sizeof(*rdma_wr));
	rdma_wr->remote_addr = queue->server_base_addr + addr;
	rdma_wr->rkey = queue->server_rkey;
	// sge
	// TODO: match the lifespan of sge and wr
	sge.addr = mind_req->sglist.dma_address;
    sge.length = len;	// mind_req->sglist.length;
    sge.lkey = queue->dev->pd->local_dma_lkey;	// mind_req->mr->lkey;
	// send wr
	memset(wr, 0, sizeof(*wr));
	wr->wr_id = (__u64)mind_req;
	wr->opcode = IB_WR_RDMA_WRITE;
	wr->sg_list = &sge;
	wr->num_sge = 1;
	wr->send_flags = IB_SEND_SIGNALED;

    ret = ib_post_send(queue->qp, wr, NULL);
    if (ret) {
        pr_err("mind_rdma_write::ib_post_send failed (%d)\n", ret);
        goto clear;
    }
	atomic_inc(&num_pending_rdma);
	return mind_req;
clear:
	kfree(mind_req);
	return NULL;
}

static struct mind_rdma_req *mind_rdma_serv_cq(void)
{
    struct mind_rdma_queue *queue = rdma_queue;

    // poll cq
	struct ib_wc wc;
	int ret = ib_poll_cq(queue->cq, 1, &wc);
	if (ret < 0)
	{
		pr_err("ib_poll_cq failed (%d)\n", ret);
		return NULL;
	}
	if (!ret)
		return NULL;	// no completion

	if (wc.status != IB_WC_SUCCESS || !wc.wr_id)
	{
		pr_err_ratelimited(
			"ib_poll_cq failed with status(%d), wr_id(%llu)\n",
			wc.status, wc.wr_id);
		return NULL;
	}

	return (struct mind_rdma_req *)wc.wr_id;
}

static int mind_rdma_create_cq(struct ib_device *ibdev,
		struct mind_rdma_queue *queue)
{
    int ret;
    int comp_vector = 0;
	// int ret, comp_vector, idx = nvme_rdma_queue_idx(queue);

	/*
	 * Spread I/O queues completion vectors according their queue index.
	 * Admin queues can always go on completion vector 0.
	 */
	// comp_vector = (idx == 0 ? idx : idx - 1) % ibdev->num_comp_vectors;

	/* Polling queues need direct cq polling context */
    queue->cq = ib_alloc_cq(ibdev, queue, queue->cq_size,
					   comp_vector, IB_POLL_DIRECT);

	if (IS_ERR(queue->cq)) {
		ret = PTR_ERR(queue->cq);
		return ret;
	}

	return 0;
}


static int mind_rdma_create_qp(struct mind_rdma_queue *queue, const int factor)
{
	struct mind_rdma_device *dev = queue->dev;
	struct ib_qp_init_attr init_attr;
	int ret;

	memset(&init_attr, 0, sizeof(init_attr));
	init_attr.cap.max_send_wr = actual_queue_size;
	init_attr.cap.max_recv_wr = actual_queue_size;
	init_attr.cap.max_recv_sge = 3;
	init_attr.cap.max_send_sge = 3;
	init_attr.sq_sig_type = IB_SIGNAL_ALL_WR;
	init_attr.qp_type = IB_QPT_RC;
	init_attr.send_cq = queue->cq;
	init_attr.recv_cq = queue->cq;
	init_attr.qp_context = queue;
	ret = rdma_create_qp(queue->cm_id, dev->pd, &init_attr);

	queue->qp = queue->cm_id->qp;
	return ret;
}

static int mind_rdma_create_queue(struct mind_rdma_queue *queue)
{
	struct ib_device *ibdev;
	const int send_wr_factor = 3;			/* MR, SEND, INV */
	int ret, pages_per_mr;

	ibdev = queue->dev->dev;
    // PD allocation
    queue->dev->pd = ib_alloc_pd(ibdev, 0);
	// queue->dev->pd = ib_alloc_pd(ibdev, IB_PD_UNSAFE_GLOBAL_RKEY);
    // ^for unsafe, use IB_PD_UNSAFE_GLOBAL_RKEY instead of 0
    if (IS_ERR(queue->dev->pd)) {
        pr_err("ib_alloc_pd failed (%ld)\n", PTR_ERR(queue->dev->pd));
        return PTR_ERR(queue->dev->pd);
    }

	/* +1 for ib_drain_qp */
	queue->cq_size = actual_queue_size * (send_wr_factor) + 1;    // (MR, SEND, INV) + 1
	ret = mind_rdma_create_cq(ibdev, queue);
	if (ret)
    {
        pr_err("mind_rdma_create_cq failed (%d)\n", ret);
		goto out_put_dev;
    }

	ret = mind_rdma_create_qp(queue, send_wr_factor);
	if (ret)
    {
        pr_err("mind_rdma_create_qp failed (%d)\n", ret);
		goto out_destroy_ib_cq;
    }

    pages_per_mr = queue->max_req_size_pages;     // say, up to 2 MB
	ret = ib_mr_pool_init(queue->qp, &queue->qp->rdma_mrs,
			      actual_queue_size,
			      IB_MR_TYPE_MEM_REG,
			      pages_per_mr, 0);
	if (ret) {
		pr_err("ib_mr_pool_init failed (%d)\n", ret);
		goto out_destroy_ring;
	}

    queue->status = STATUS_QUEUE_CREATED;
	return 0;

out_destroy_ring:
	rdma_destroy_qp(queue->cm_id);
out_destroy_ib_cq:
	ib_free_cq(queue->cq);
out_put_dev:
    // TODO: ref checking for multiple queues sharing the PD
	ib_dealloc_pd(queue->dev->pd);
	return ret;
}

static int mind_rdma_addr_resolved(struct mind_rdma_queue *queue)
{
    int ret;

    // Create CQ, QP; ref: nvme_rdma_addr_resolved, nvme_rdma_create_queue_ib
    ret = mind_rdma_create_queue(queue);
    if (ret) {
        pr_info("mind_rdma_create_queue failed (%d)\n", ret);
        return ret;
    }

    // Onitiate resolving route; ref: rdma_resolve_route
    ret = rdma_resolve_route(queue->cm_id, MIND_RDMA_CM_TIMEOUT_MS);
    if (ret) {
        pr_info("rdma_resolve_route failed (%d)\n", ret);
        return ret;
    }

    return 0;
}


static int mind_rdma_route_resolved(struct mind_rdma_queue *queue)
{
	struct rdma_conn_param param = { };
	struct mr_info priv;
	int ret;

	// Initialize private data properly
	memset(&priv, 0, sizeof(priv));
	priv.remote_addr = 0;  // Client doesn't have remote addr yet
	priv.rkey = 0;         // Client doesn't have rkey yet
	priv.mem_size = queue->server_mem_size;

	pr_info("Sending private data: mem_size=%llu, sizeof(mr_info)=%zu\n",
	        priv.mem_size, sizeof(priv));

	param.flow_control = 0;	// 1;

	// == Use actual device-determined queue size
	param.responder_resources = actual_queue_size;
	param.initiator_depth = actual_queue_size;
	pr_info("Connection params: responder_resources=%d, initiator_depth=%d",
		param.responder_resources, param.initiator_depth);

	/* maximum retry count */
	param.retry_count = 7;
	param.rnr_retry_count = 7;
	param.private_data = &priv;
	param.private_data_len = sizeof(priv);

	ret = rdma_connect_locked(queue->cm_id, &param);
	if (ret) {
		pr_err("rdma_connect_locked failed (%d).\n", ret);
		return ret;
	}
	return 0;
}

static int mind_rdma_established(struct mind_rdma_queue *queue, struct rdma_cm_event *ev)
{
	struct mr_info *server_info = (struct mr_info *)ev->param.conn.private_data;
	if (!server_info)
	{
		pr_err("server_info is NULL\n");
		return -EINVAL;
	}
	queue->server_base_addr = server_info->remote_addr;
	queue->server_rkey = server_info->rkey;
	pr_info("RDMA connection established::VA=0x%llx\n", queue->server_base_addr);
	return 0;
}

static int mind_rdma_cm_handler(struct rdma_cm_id *cm_id,
		struct rdma_cm_event *ev)
{
	struct mind_rdma_queue *queue = cm_id->context;
	int cm_error = 0;
    if (!queue)
    {
        pr_err("rdma_queue is NULL\n");
        return -EINVAL;
    }

	pr_info("%s (%d): status %d id %p\n",
		rdma_event_msg(ev->event), ev->event,
		ev->status, cm_id);

	switch (ev->event) {
	case RDMA_CM_EVENT_ADDR_RESOLVED:
		cm_error = mind_rdma_addr_resolved(queue);
		break;
	case RDMA_CM_EVENT_ROUTE_RESOLVED:
	    cm_error = mind_rdma_route_resolved(queue);
		break;
	case RDMA_CM_EVENT_ESTABLISHED:
		cm_error = mind_rdma_established(queue, ev);
		complete(&queue->cm_done);
		return 0;
	case RDMA_CM_EVENT_REJECTED:
		pr_err("RDMA connection rejected - status: %d, private_data_len: %d\n",
		       ev->status, ev->param.conn.private_data_len);
		cm_error = -ECONNREFUSED;
		break;
	case RDMA_CM_EVENT_ROUTE_ERROR:
	case RDMA_CM_EVENT_CONNECT_ERROR:
	case RDMA_CM_EVENT_UNREACHABLE:
	case RDMA_CM_EVENT_ADDR_ERROR:
		pr_err("RDMA CM error event %d, status: %d\n", ev->event, ev->status);
		cm_error = -ECONNRESET;
		break;
	case RDMA_CM_EVENT_DISCONNECTED:
		break;
	default:
		pr_err("Unexpected RDMA CM event (%d)\n", ev->event);
		break;
	}

	if (cm_error) {
		queue->cm_error = cm_error;
		complete(&queue->cm_done);
	}

	return 0;
}

static int mind_rdma_wait_for_cm(struct mind_rdma_queue *queue)
{
	int ret;
	ret = wait_for_completion_interruptible(&queue->cm_done);
	if (ret)
		return ret;
	WARN_ON_ONCE(queue->cm_error > 0);
	return queue->cm_error;
}

struct mind_rdma_req *poll_cq(void)
{
	unsigned int cnt = 0;
	struct mind_rdma_req *res = mind_rdma_serv_cq();
	return res;
}

static int poll_and_check_cq(struct mind_rdma_req *target)
{
	unsigned int cnt = 0;
	struct mind_rdma_req *res = mind_rdma_serv_cq();
	while (!res)
	{
		usleep_range(MIND_RDMA_CQ_POLL_US, MIND_RDMA_CQ_POLL_US);
		res = mind_rdma_serv_cq();
		cnt ++;
		if (cnt > MIND_RMDA_CQ_POLL_CNT)	// 1 ms
		{
			break;
		}
	}

	if (!res || (res && res != target))
	{
		pr_info("task mismatch::0x%lx <-> 0x%lx\n", (unsigned long)res, (unsigned long)target);
		return -1;
	}
	return 0;
}

static void mind_rdma_init_test(void)
{
	struct mind_rdma_req *res = NULL;
	void *buf = kmalloc(PAGE_SIZE, GFP_KERNEL);
	unsigned long addr = PAGE_SIZE;
	unsigned long len = PAGE_SIZE;
	// read from the second page
	((unsigned long *)buf)[0] = 0x12;
	// pr_info("RDMA init:: 0x%lx\n", ((unsigned long *)buf)[0]);
	res = mind_rdma_read(NULL, (u64)NULL, buf, addr, len);
	if (poll_and_check_cq(res))
		goto clear_test;
	if (((unsigned long *)buf)[0] != 0x0)
		pr_info("RDMA read failed:: 0x%lx\n", ((unsigned long *)buf)[0]);
	finish_mind_req(res);

	// write to the second page
	((unsigned long *)buf)[0] = 0x42;
	res = mind_rdma_write(NULL,(u64)NULL, buf, addr, len);
	if (poll_and_check_cq(res))
		goto clear_test;
	// pr_info("RDMA write:: 0x%lx\n", ((unsigned long *)buf)[0]);
	finish_mind_req(res);

	// read from the second page
	((unsigned long *)buf)[0] = 0x0;
	res = mind_rdma_read(NULL, (u64)NULL, buf, addr, len);
	if (poll_and_check_cq(res))
		goto clear_test;
	if (((unsigned long *)buf)[0] != 0x42)
		pr_info("RDMA read failed:: 0x%lx\n", ((unsigned long *)buf)[0]);
	finish_mind_req(res);

clear_test:
	// kfree(task);
	kfree(buf);
}

static int mind_rdma_add(struct ib_device *ib_device)
{
	int ret = 0;
	extern char* rdma_device_name;  // Module parameter from mind_ram_drv_rdma.c

	// assert rdma_queue is not null
	if (!rdma_queue)
	{
		pr_err("rdma_queue is NULL\n");
		return -EINVAL;
	}

	// Device selection logic
	if (rdma_device_name && strcmp(ib_device->name, rdma_device_name) != 0) {
		pr_info("Skipping RDMA device %s (not matching specified device %s)\n",
		        ib_device->name, rdma_device_name);
		return 0;  // Not an error, just skip this device
	}

	// If no device specified, use the first suitable device, but skip if already initialized
	if (!rdma_device_name && rdma_queue->dev) {
		pr_info("RDMA device already selected (%s), skipping %s\n",
		        rdma_queue->dev->dev->name, ib_device->name);
		return 0;  // Already have a device
	}

	pr_info("Using RDMA device: %s\n", ib_device->name);
	// configure address
	ret = inet_pton_with_scope(&init_net, AF_UNSPEC,
			rdma_queue->server_ip, rdma_queue->server_port, &rdma_queue->server_addr);
	if (ret) {
		pr_err("malformed address passed: %s:%s\n",
			rdma_queue->server_ip, rdma_queue->server_port);
		return -EINVAL;
	}

	// Log device capabilities for debugging
	pr_info("Device capabilities: max_qp_wr=%d, max_qp_rd_atom=%d, max_qp_init_rd_atom=%d\n",
	        ib_device->attrs.max_qp_wr, ib_device->attrs.max_qp_rd_atom,
	        ib_device->attrs.max_qp_init_rd_atom);

	// try to configure with rdma cm
	rdma_queue->dev = kzalloc(sizeof(*rdma_queue->dev), GFP_KERNEL);
	rdma_queue->dev->dev = ib_device;
	init_completion(&rdma_queue->cm_done);
	rdma_queue->cm_id = rdma_create_id(&init_net, mind_rdma_cm_handler, rdma_queue,
			RDMA_PS_TCP, IB_QPT_RC);
	if (IS_ERR(rdma_queue->cm_id)) {
		pr_err("failed to create CM ID: %ld\n", PTR_ERR(rdma_queue->cm_id));
		ret = PTR_ERR(rdma_queue->cm_id);
		return -EINVAL;
	}

	// server ip
	ret = rdma_resolve_addr(rdma_queue->cm_id, NULL,	// src address = NULL; not required
		(struct sockaddr *)&rdma_queue->server_addr,
		MIND_RDMA_CM_TIMEOUT_MS);
	if (ret) {
		pr_info(
			"rdma_resolve_addr failed (%d).\n", ret);
		goto out_destroy_cm_id;
	}
    // NOTE) mind_rdma_cm_handler will trigger route resolution and final initialization
    //       so wait...
	// wait for cm handler
	ret = mind_rdma_wait_for_cm(rdma_queue);
	if (ret) {
		pr_info(
			"rdma connection establishment failed (%d)\n", ret);
		goto out_destroy_cm_id;
	}
	// TODO:
	pr_info("RDMA CM ID created\n");
	// mind_rdma_init_test();
	pr_info("RDMA test completed (no output = success)\n");
	complete(&rdma_queue->init_done);
	return 0;

	// errors
out_destroy_cm_id:
	rdma_destroy_id(rdma_queue->cm_id);
	return ret;
}


static void mind_rdma_remove(struct ib_device *ib_device, void *client_data)
{
	// Only remove if this is the device we're actually using
	if (rdma_queue && rdma_queue->dev && rdma_queue->dev->dev == ib_device)
	{
		pr_info("Removing RDMA device: %s\n", ib_device->name);
		// // remove the connection manager
		pr_info("Disconnecting RDMA queue...\n");
		rdma_disconnect(rdma_queue->cm_id);
		ib_drain_qp(rdma_queue->qp);

        rdma_destroy_id(rdma_queue->cm_id);
        pr_info("Removing RDMA queue...\n");
        // check queue status and destory them if needed
        if (rdma_queue->status == STATUS_QUEUE_CREATED)
        {
            ib_mr_pool_destroy(rdma_queue->qp, &rdma_queue->qp->rdma_mrs);
            rdma_destroy_qp(rdma_queue->cm_id);
            ib_free_cq(rdma_queue->cq);
            // TODO: ref checking for multiple queues sharing the PD
            ib_dealloc_pd(rdma_queue->dev->pd);
        }
        pr_info("RDMA queue destroyed\n");
		// unmap device
        if (rdma_queue->dev)
		{
			kfree(rdma_queue->dev);
			rdma_queue->dev = NULL;
		}

        pr_info("RDMA CM ID destroyed\n");
		kfree(rdma_queue);
		rdma_queue = NULL;
	}
	else if (rdma_queue && rdma_queue->dev)
	{
		pr_info("Skipping removal of RDMA device %s (not our active device %s)\n",
		        ib_device->name, rdma_queue->dev->dev->name);
	}
}

struct ib_client mind_rdma_ib_client = {
	.name   = "mind_ram_rdma",
	.add	= mind_rdma_add,
	.remove = mind_rdma_remove
};
