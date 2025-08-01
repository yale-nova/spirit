echo "Warning: you must first start memory side program before activating swap device in the compute node!"
echo "If you are seeing the Jupyter notebook, it is likely that you have already started it."
echo "Sleep for 10 sec; cancel with CTRL+C now if you haven't started it yet."
sleep 10
export SPIRIT_PATH="/opt/spirit"
cd $SPIRIT_PATH/spirit-controller/remote_mem/drivers/mind_ram
make

cd $SPIRIT_PATH/spirit-controller/remote_mem/scripts
RDMA_DEVICE="mlx5_3" ./setup_mind_ram.sh