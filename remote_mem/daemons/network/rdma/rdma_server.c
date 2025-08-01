#include "rdma_server.h"

#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <fcntl.h>
#include <unistd.h>

static size_t serv_buffer_size;
static struct rdma_cm_id *listener;
static int client_connected = 0;

#ifndef MAP_HUGE_1GB
#define MAP_HUGE_1GB    (30 << MAP_HUGE_SHIFT)
#endif

void server_init(char *server_ip, uint32_t server_port)
{
	memset(&addr, 0, sizeof(addr));
	addr.sin_family = AF_INET;
	addr.sin_port = htons(server_port);
	inet_pton(AF_INET, server_ip, &addr.sin_addr);

	// Create event channel
	printf("Creating event channel...\n");
	ec = rdma_create_event_channel();
	if (!ec) {
		perror("rdma_create_event_channel");
		exit(1);
	}

	// Create RDMA ID for listening
	printf("Creating RDMA ID...\n");
	if (rdma_create_id(ec, &listener, NULL, RDMA_PS_TCP)) {
		perror("rdma_create_id");
		exit(1);
	}

	// Bind address to RDMA ID
	printf("Binding address...\n");
	if (rdma_bind_addr(listener, (struct sockaddr *)&addr)) {
		perror("rdma_bind_addr");
		exit(1);
	}

	// Start listening for incoming connections
	printf("Listening...\n");
	if (rdma_listen(listener, 10)) {
		perror("rdma_listen");
		exit(1);
	}

	printf("Server is listening at %s:%u\n", server_ip, server_port);

	// Accept incoming connection and process client requests
	printf("Waiting for connection...\n");
	if (rdma_get_cm_event(ec, &event)) {
		perror("rdma_get_cm_event");
		exit(1);
	}

	if (event->event != RDMA_CM_EVENT_CONNECT_REQUEST) {
		fprintf(stderr, "Unexpected event: %s\n",
			rdma_event_str(event->event));
		exit(1);
	} else {
		uint64_t client_addr;
		uint32_t client_rkey;
		struct mr_info *client_mr =
			(struct mr_info *)event->param.conn.private_data;

		printf("Received connection request with private_data_len: %d\n",
		       event->param.conn.private_data_len);
		printf("Expected mr_info size: %zu\n", sizeof(struct mr_info));

		if (client_mr == NULL) {
			fprintf(stderr, "Private data is NULL\n");
			exit(1);
		}

		if (event->param.conn.private_data_len != sizeof(struct mr_info)) {
			fprintf(stderr, "Private data size mismatch: got %d, expected %zu\n",
			        event->param.conn.private_data_len, sizeof(struct mr_info));
			// Continue anyway to see if we can handle it
		}
		memcpy(&client_addr, &client_mr->remote_addr,
		       sizeof(client_addr));
		memcpy(&client_rkey, &client_mr->rkey, sizeof(client_rkey));
		memcpy(&serv_buffer_size, &client_mr->mem_size, sizeof(serv_buffer_size));

		printf("client_addr: %lx\n", client_addr);
		printf("client_rkey: %u\n", client_rkey);
		printf("serv_buffer_size: %lx\n", serv_buffer_size);
	}
	conn = event->id;

	rdma_init_finish();

	server_connect();
}

void server_connect(void)
{
	rdma_ack_cm_event(event);
	printf("Allocating buffer and registering memory...\n");
	// Align size to huge page boundary (2MB)
    serv_buffer_size = (serv_buffer_size + 2097151) & ~2097151;

    // Direct huge page allocation - no file needed
    buffer = mmap(NULL, serv_buffer_size, PROT_READ | PROT_WRITE,
                MAP_PRIVATE | MAP_ANONYMOUS | MAP_HUGETLB | MAP_HUGE_1GB ,
                -1, 0);
	// buffer = mmap(NULL, serv_buffer_size, PROT_READ | PROT_WRITE,
	// 	      MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	if (buffer == MAP_FAILED) {
		perror("mmap");
		exit(1);
	}
	printf("server addr: %lx\n", (uintptr_t)buffer);
	memset(buffer, 0, serv_buffer_size);
	mr = ibv_reg_mr(pd, buffer, serv_buffer_size,
			IBV_ACCESS_LOCAL_WRITE | IBV_ACCESS_REMOTE_WRITE |
				IBV_ACCESS_REMOTE_READ);
	if (!mr) {
		perror("ibv_reg_mr");
		exit(1);
	}
	printf("server key: %u\n", mr->rkey);

	// Accept RDMA connection
	printf("Accepting RDMA connection...\n");

	// Check device capabilities
	struct ibv_device_attr device_attr;
	if (ibv_query_device(conn->verbs, &device_attr) == 0) {
		printf("Device capabilities: max_qp_wr=%d, max_qp_rd_atom=%d, max_qp_init_rd_atom=%d\n",
		       device_attr.max_qp_wr, device_attr.max_qp_rd_atom, device_attr.max_qp_init_rd_atom);
	}

	struct rdma_conn_param cm_params = { 0 };
	struct mr_info mr_info = { (uintptr_t)buffer, mr->rkey };
	cm_params.private_data = &mr_info;
	cm_params.private_data_len = sizeof(mr_info);

	// Use actual queue size determined by device capabilities
	cm_params.responder_resources = actual_queue_size;  // Use device-determined size
	cm_params.initiator_depth = actual_queue_size;      // Use device-determined size

	printf("Connection params: responder_resources=%d, initiator_depth=%d\n",
	       cm_params.responder_resources, cm_params.initiator_depth);
	if (!conn) {
		printf("conn is NULL\n");
		return;
	}
	if (conn->verbs) {
		printf("Verbs context: %p\n", conn->verbs);
	} else {
		printf("Verbs context: NULL\n");
	}

	// Printing Queue Pair (QP) details
	if (conn->qp) {
		printf("QP Number: %d\n", conn->qp->qp_num);
	} else {
		printf("QP: NULL\n");
	}

	// Printing Port Space
	printf("Port Space: %d\n", conn->ps);

	// Printing Port Number
	printf("Port Number: %u\n", conn->port_num);
	if (rdma_accept(conn, &cm_params)) {
		perror("rdma_accept");
		exit(1);
	}

	// Get CM event
	printf("Getting CM event...\n");
	if (rdma_get_cm_event(ec, &event)) {
		perror("rdma_get_cm_event");
		exit(1);
	}

	if (event->event != RDMA_CM_EVENT_ESTABLISHED) {
		fprintf(stderr, "Unexpected event: %s\n",
			rdma_event_str(event->event));
		exit(1);
	}
	rdma_ack_cm_event(event);
	//post_receive();
	printf("connected\n");
	client_connected = 1;
}

void server_disconnect(void)
{
	if (!client_connected) {
		return;
	}
	client_connected = 0;
	// check_cm_event();	// should wait inside the main logic
	printf("server Disconnecting and cleaning up\n");

	rdma_destroy_qp(conn);
	puts("destroyed qp");

	rdma_destroy_id(conn);
	puts("destroyed conn");
	conn = NULL;

	ibv_destroy_cq(cq);
	cq = NULL;
	puts("destroyed cq");

	ibv_dereg_mr(mr);
	mr = NULL;
	printf("Destroyed mr\n");

	ibv_dealloc_pd(pd);
	pd = NULL;

	rdma_destroy_id(listener);
	listener = NULL;

	rdma_destroy_event_channel(ec);
	ec = NULL;
}

void server_release_buffer(void)
{
	if (buffer != NULL)
	{
		munmap(buffer, serv_buffer_size);
		buffer = NULL;
	}
}

// Function to post a receive work request
void post_receive()
{
	struct ibv_recv_wr recv_wr, *bad_recv_wr = NULL;
	struct ibv_sge recv_sge;
	memset(&recv_wr, 0, sizeof(recv_wr));
	recv_wr.wr_id = 1;
	recv_sge.addr = (uintptr_t)buffer;
	recv_sge.length = serv_buffer_size;
	recv_sge.lkey = mr->lkey;
	recv_wr.sg_list = &recv_sge;
	recv_wr.num_sge = 1;
	if (ibv_post_recv(conn->qp, &recv_wr, &bad_recv_wr)) {
		perror("ibv_post_recv");
		exit(1);
	}
}
