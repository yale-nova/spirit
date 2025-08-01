#ifndef RDMA_CLIENT_H
#define RDMA_CLIENT_H

#include "rdma_common.h"

void server_init(char *server_ip, uint32_t server_port);

void server_connect(void);

void server_disconnect(void);

void server_release_buffer(void);

void post_receive();

#endif
