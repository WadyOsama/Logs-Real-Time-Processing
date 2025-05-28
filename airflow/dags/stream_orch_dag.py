from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import BranchPythonOperator
from airflow.operators.empty import EmptyOperator
import requests

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'start_date': datetime(2025, 1, 1),
    'retries': 1,
    'retry_delay': timedelta(seconds=5),
    'email_on_failure': False,
    'email_on_retry': False,
    'email_on_success': False,
    'on_failure_callback': None,
    'on_success_callback': None,
    'on_retry_callback': None,
    'concurrency': 1,
    'max_active_runs': 1,
    'max_active_tasks': 1,
    'catchup': False,
    'tags': ['streaming', 'orchestration'],
    }

def _check_pyspark_job():
    """
    Check if the PySpark job is running by querying the Spark REST API.
    If the job is running, return 'skip_submit_job_task'.
    If the job is not running, return 'submit_spark_job_task'.
    If there is an error in the request, also return 'submit_spark_job_task'.
    """
    try:
        response = requests.get('http://spark-master:8080/json/')
        if response.status_code == 200:
            active_job_status = response.json()["activeapps"]
            for job in active_job_status:
                if job['name'] == 'RealTimeLogProcessor':
                    print("PySpark job is running...")
                    return "skip_submit_job_task"
        print("PySpark job is not running...")
        return "submit_spark_job_task"
    except requests.RequestException as e:
        print(f"Error checking PySpark job: {e}")
        return "submit_spark_job_task"

with DAG(
    dag_id='stream_orch_dag',
    default_args=default_args,
    schedule_interval="*/1 * * * *",  # Set to None for manual triggering
    catchup=False,
) as dag:

    # Task to check if the topic exists and create it if not
    create_payment_topic_task = BashOperator(
        task_id='create_payment_topic_task',
        bash_command=(
        "if ! docker exec kafka1 kafka-topics --list --bootstrap-server kafka1:29092 | grep -q '^payment$'; then "
        "docker exec kafka1 kafka-topics --create --topic payment --partitions 3 --replication-factor 3 --bootstrap-server kafka1:29092; "
        "else echo 'Topic payment already exists'; fi"
        ),
    )

    # Task to start the log producer python script
    start_log_producer_task = BashOperator(
        task_id='start_log_producer_task',
        bash_command=(
        "if ! docker ps | grep 'log-generator'; then "
        "docker start log-generator; "
        "else echo 'Logs producer already running'; fi"
        ),
    )

    # Task to check if the pyspark job is running
    check_pyspark_job_branch = BranchPythonOperator(
        task_id='check_pyspark_job_task',
        python_callable=_check_pyspark_job,
    )

    submit_spark_job_task = BashOperator(
        task_id='submit_spark_job_task',
        bash_command=(
        "docker exec -d spark-worker spark-submit --master spark://spark-master:7077 --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0 /opt/spark-apps/stream_processing.py"
        ),
    )

    skip_submit_job_task = EmptyOperator(task_id="skip_submit_job_task")

    create_payment_topic_task >> start_log_producer_task >> check_pyspark_job_branch
    check_pyspark_job_branch >> [submit_spark_job_task, skip_submit_job_task]
    