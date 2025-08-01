FROM python:3.9 
# Or any preferred Python version.
# ADD main.py .

RUN pip install requests scikit-learn numpy pandas redis argparse joblib

ENTRYPOINT ["/usr/mind_res_alloc/docker_init.sh"]
# CMD [“python”, “./main.py”] 
