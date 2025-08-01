# set the name of docker container
CONTAINER_NAME = mind_res_alloc

docker_build:
	docker build -t mind_res_alloc .

docker_run:
	docker run --rm -v .:/usr/mind_res_alloc/ --name $(CONTAINER_NAME) mind_res_alloc 

docker_stop:
	docker stop $(CONTAINER_NAME)

memcached_alloc:
	python main_memcached.py