#include "rdma_common.h"
#include <stdlib.h>
#include <stdio.h>

struct sockaddr_in addr;
struct rdma_event_channel *ec = NULL;
struct ibv_qp_init_attr qp_attr;
struct rdma_cm_id *conn = NULL;
struct ibv_pd *pd;
struct ibv_mr *mr;
struct ibv_cq *cq;
char *buffer = NULL;
uint64_t buffer_size = 0;
atomic_uint *alloc_array = NULL;

// Actual queue size determined by device capabilities
int actual_queue_size = 16;  // Default fallback

struct rdma_cm_event *event;

void rdma_deinit(void)
{
	printf("Disconnecting and cleaning up\n");
	check_cm_event();

	rdma_destroy_qp(conn);

	puts("destroyed qp");

	puts("destroyed conn");

	ibv_destroy_cq(cq);

	puts("destroyed cq");

	printf("Destroying mr\n");
	ibv_dereg_mr(mr);

	ibv_dealloc_pd(pd);

	rdma_destroy_event_channel(ec);
}

enum rdma_cm_event_type check_cm_event(void)
{
	printf("Checking CM event...\n");
	if (rdma_get_cm_event(ec, &event)) {
		perror("Failed to retrieve a cm event\n");
		exit(1);
	}
	fprintf(stdout, "Rceived event: %s\n", rdma_event_str(event->event));
	rdma_ack_cm_event(event);
	puts("ack!");
	return event->event;
}

void rdma_init(void)
{
	printf("Creating event channel...\n");
	ec = rdma_create_event_channel();
	if (!ec) {
		perror("rdma_create_event_channel");
		exit(1);
	}

	printf("Creating RDMA ID...\n");
	if (rdma_create_id(ec, &conn, NULL, RDMA_PS_TCP)) {
		perror("rdma_create_id");
		exit(1);
	}
}

void rdma_init_finish(void)
{
	// Allocate Protection Domain
	printf("Allocating PD...\n");
	pd = ibv_alloc_pd(conn->verbs);
	if (!pd) {
		perror("ibv_alloc_pd");
		exit(1);
	}

	// Query device capabilities and determine actual queue size
	struct ibv_device_attr device_attr;
	if (ibv_query_device(conn->verbs, &device_attr) == 0) {
		int max_rd_atom = device_attr.max_qp_rd_atom;
		int max_init_rd_atom = device_attr.max_qp_init_rd_atom;
		actual_queue_size = (MIND_QUEUE_SIZE_MAX < max_rd_atom) ? MIND_QUEUE_SIZE_MAX : max_rd_atom;
		actual_queue_size = (actual_queue_size < max_init_rd_atom) ? actual_queue_size : max_init_rd_atom;

		printf("Device capabilities: max_qp_wr=%d, max_qp_rd_atom=%d, max_qp_init_rd_atom=%d\n",
		       device_attr.max_qp_wr, max_rd_atom, max_init_rd_atom);
		printf("Using actual_queue_size=%d (min of max=%d, rd_atom=%d, init_rd_atom=%d)\n",
		       actual_queue_size, MIND_QUEUE_SIZE_MAX, max_rd_atom, max_init_rd_atom);
	}

	printf("Creating CQ...\n");
	cq = ibv_create_cq(conn->verbs, 3 * actual_queue_size + 1, NULL, NULL, 0);

	printf("Creating QP...\n");
	memset(&qp_attr, 0, sizeof(qp_attr));
	qp_attr.qp_type = IBV_QPT_RC;
	qp_attr.send_cq = cq;
	qp_attr.recv_cq = cq;
	qp_attr.cap.max_send_wr = actual_queue_size;
	qp_attr.cap.max_recv_wr = actual_queue_size;
	qp_attr.cap.max_send_sge = 3;
	qp_attr.cap.max_recv_sge = 3;
	if (rdma_create_qp(conn, pd, &qp_attr)) {
		perror("rdma_create_qp");
		exit(1);
	}
}
