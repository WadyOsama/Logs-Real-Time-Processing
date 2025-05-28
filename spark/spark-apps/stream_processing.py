
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, udf, from_json, expr, when, lit, regexp_extract, to_timestamp, window
from pyspark.sql.types import StructType, StructField, StringType, DoubleType, TimestampType, BooleanType # Added more types
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.json_schema import JSONDeserializer
from confluent_kafka.serialization import SerializationContext
from datetime import datetime
import os

spark = SparkSession.builder \
    .appName("RealTimeLogProcessor") \
    .config("spark.jars.packages", "org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0") \
    .config("spark.sql.streaming.schemaInference", "true") \
    .getOrCreate()

spark.sparkContext.setLogLevel("ERROR") # Reduce verbosity

print("Spark Session Created. Reading from Kafka...")

# --- Configuration ---
KAFKA_BROKER_URL = 'kafka1:29092,kafka2:29093,kafka3:29094'
KAFKA_TOPIC_NAME = 'payment'
SCHEMA_REGISTRY_URL = "http://schema-registry:8081"

LOG_EVENT_SCHEMA = StructType([
    StructField("timestamp", StringType(), True),
    StructField("level", StringType(), True),
    StructField("message", StringType(), True)
])

PAYMENT_MESSAGE_SCHEMA = StructType([
    StructField("type", StringType(), True), # e.g., "Payment"
    StructField("is_failed", BooleanType(), True), # e.g., "FAILED" or "successful"
    StructField("order_id", StringType(), True), # e.g., "order_c1799bede811"
    StructField("amount", DoubleType(), True), # e.g., "824.23" from "Amount: $824.23."
    StructField("user_id", StringType(), True), # e.g., "user_000659"
    StructField("ip_address", StringType(), True), # e.g., "
    StructField("payment_id", StringType(), True), # e.g., "payment_1234567890"
    StructField("fail_reason", StringType(), True) # e.g., "Insufficient funds"
])

OUTPUT_PATH_AGG_LOGS = "/opt/spark-data/data/aggregated_logs"
OUTPUT_PATH_PROCESSED_LOGS_HOURLY_BASE = "/opt/spark-data/data/processed_logs_hourly"
CHECKPOINT_PATH = "/opt/spark-data/spark_checkpoints/processed_logs_checkpoint"
CHECKPOINT_PATH_ALERTS = "/opt/spark-data/spark_checkpoints/alerts_checkpoint"

os.makedirs(OUTPUT_PATH_AGG_LOGS, mode=0o777, exist_ok=True)
os.makedirs(OUTPUT_PATH_PROCESSED_LOGS_HOURLY_BASE, mode=0o777, exist_ok=True)
os.makedirs(CHECKPOINT_PATH, mode=0o777, exist_ok=True)
os.makedirs(CHECKPOINT_PATH_ALERTS, mode=0o777, exist_ok=True)

# Global or broadcasted Schema Registry client and deserializer
_schema_registry_client = None
_json_deserializer = None

def get_json_deserializer():
    global _schema_registry_client, _json_deserializer
    if _json_deserializer is None:
        if _schema_registry_client is None:
            # Create a Schema Registry configuration
            sr_config = {'url': SCHEMA_REGISTRY_URL}

            # Initialize Schema Registry Client
            _schema_registry_client = SchemaRegistryClient(sr_config)

        # Initialize JSONDeserializer.
        _json_deserializer = JSONDeserializer(
            schema_str=None, # Schema will be inferred from the data
            schema_registry_client=_schema_registry_client,
            from_dict=lambda data, ctx: data # Ensures it returns the dict as is
        )
    return _json_deserializer

# UDF to deserialize JSON Schema messages
def deserialize_json_value(value_bytes, topic_name):
    if value_bytes is None:
        return None
    try:
        deserializer = get_json_deserializer()
        context = SerializationContext(topic_name, 'value')
        # The deserializer handles the magic byte, schema ID, and JSON parsing
        return deserializer(value_bytes, context) # Returns a Python dict
    except Exception as e:
        # Log the error or return a specific structure indicating an error
        print(f"Error deserializing JSON Schema message: {e}, value (first 100 bytes): {value_bytes[:100]}...")
        return None

deserialize_json_udf = udf(deserialize_json_value, LOG_EVENT_SCHEMA) # UDF returns our defined StructType


# Read from Kafka
kafka_df = spark.readStream \
    .format("kafka") \
    .option("kafka.bootstrap.servers", KAFKA_BROKER_URL) \
    .option("subscribe", KAFKA_TOPIC_NAME) \
    .option("startingOffsets", "earliest") \
    .option("failOnDataLoss", "false") \
    .load()

deserialize_json_udf = udf(deserialize_json_value, LOG_EVENT_SCHEMA) # UDF returns our defined StructType

deserialized_df = kafka_df.withColumn(
    "deserialized_value",
        deserialize_json_udf(col("value"), col("topic"))
)


base_df = deserialized_df \
    .select(
        to_timestamp(col("deserialized_value.timestamp")).alias("event_time"), # Convert log's timestamp
        col("deserialized_value.level").alias("level"),
        col("deserialized_value.message").alias("message")
    )

base_df_with_watermark = base_df.withWatermark("event_time", "5 seconds") # Allow 5 seconds for late data

def parse_message_details(message_str):

    message_list = message_str.split()
    type = message_list[0] # e.g., "Payment"
    is_failed = True if message_list[1] == "FAILED" else False # e.g., "FAILED" or "successful"
    order_id = message_list[4].rstrip('.') # e.g., "order_c1799bede811"
    amount = float(message_list[6].lstrip('$').rstrip('.')) # e.g., "824.23" from "Amount: $824.23."
    user_id = message_list[8].rstrip('.') # e.g., "user_000659"
    ip_address = message_list[-1].rstrip('.') # e.g., "192.168.0.1"
    payment_id = message_list[11].rstrip('.') if is_failed == False else None
    fail_reason = " ".join(message_list[10:-2]).rstrip('.') if is_failed == 1 else None

    return (type, is_failed, order_id, amount, user_id, ip_address, payment_id, fail_reason)

parse_message_udf = udf(parse_message_details, PAYMENT_MESSAGE_SCHEMA)

enriched_df = base_df_with_watermark.withColumn("parsed_message", parse_message_udf(col("message"))) \
    .select(
        "event_time",
        "level",
        col("parsed_message.type").alias("type"),
        col("parsed_message.is_failed").alias("is_failed"),
        col("parsed_message.order_id").alias("order_id"),
        col("parsed_message.amount").alias("amount"),
        col("parsed_message.user_id").alias("user_id"),
        col("parsed_message.ip_address").alias("ip_address"),
        col("parsed_message.payment_id").alias("payment_id"),
        col("parsed_message.fail_reason").alias("fail_reason")
    )

enriched_df

# Aggregate over a tumbling window (e.g., 1 minute window)
aggregated_df = enriched_df \
    .groupBy(
        window(col("event_time"), "1 minute"), # 1 min window
        col("type"),
        col("is_failed")
    ) \
    .agg(
        expr("count(*) as event_count"),
        expr("sum(amount) as total_amount"),
    )

def process_agg_and_alerts_batch(batch_df, batch_id):
    if batch_df.isEmpty():
        print(f"Batch {batch_id}: No data in aggregated_df batch to write.")
        return

    # Determine the filename based on the current processing time's day
    current_processing_time = datetime.now()
    # Format: YYYY-MM-DD (e.g., 2023-10-27)
    daily_file_suffix = current_processing_time.strftime("%Y-%m-%d")
    output_file_path = os.path.join(OUTPUT_PATH_AGG_LOGS, f"aggregated_data_{daily_file_suffix}.csv")

    print(f"Batch {batch_id}: Writing aggregated_df batch to {output_file_path}")

    # Coalesce to 1 partition before collecting to Pandas
    # This means all data for THIS BATCH for THIS HOUR goes into one write operation from the driver.
    pandas_df = batch_df \
        .select(["window.start", "window.end", "type", "is_failed", "event_count", "total_amount"]) \
        .coalesce(1) \
        .toPandas()

    if not pandas_df.empty:
        file_exists = os.path.exists(output_file_path)
        # Write header only if file doesn't exist or is empty
        write_header = not file_exists or (file_exists and os.path.getsize(output_file_path) == 0)

        pandas_df.to_csv(output_file_path, mode='a', header=write_header, index=False)
        print(f"Batch {batch_id}: Appended {len(pandas_df)} rows to {output_file_path}. Header written: {write_header}")
    else:
        print(f"Batch {batch_id}: Pandas DataFrame for enriched_df was empty. No data written to {output_file_path}.")

    alerts_to_send = []
    
    for row in pandas_df.itertuples(index=False):
        if (row.type == "Payment" and row.is_failed == True) and (row.event_count >= 10 or row.total_amount == 10000):
            alert_message = f"ALERT: High Payment error rate! {row.event_count} errors with ${row.total_amount}."
            alerts_to_send.append(alert_message)
            # In a real system, send this alert (email, Slack, etc.)
            print(f"\033[91m{alert_message}\033[0m") # Red color for console

        if (row.type == "Refund" and row.is_failed == False) and (row.event_count >= 10 or row.total_amount == 10000):
            alert_message = f"ALERT: High Refund requests rate! {row.event_count} requests with ${row.total_amount}$."
            alerts_to_send.append(alert_message)
            # In a real system, send this alert (email, Slack, etc.)
            print(f"\033[91m{alert_message}\033[0m") # Red color for console

    if not alerts_to_send:
        print("No alerts for this batch.")
    print("--- Finished Processing Alerts Batch ---")

# Function to process each micro-batch for enriched_df and write to an hourly CSV
def write_enriched_to_hourly_csv(batch_df, batch_id):
    if batch_df.isEmpty():
        print(f"Batch {batch_id}: No data in enriched_df batch to write.")
        return

    # Determine the filename based on the current processing time's hour
    current_processing_time = datetime.now()
    # Format: YYYY-MM-DD_HH (e.g., 2023-10-27_15 for 3 PM)
    hourly_file_suffix = current_processing_time.strftime("%Y-%m-%d_%H")
    output_file_path = os.path.join(OUTPUT_PATH_PROCESSED_LOGS_HOURLY_BASE, f"enriched_data_{hourly_file_suffix}.csv")

    print(f"Batch {batch_id}: Writing enriched_df batch to {output_file_path}")

    # Coalesce to 1 partition before collecting to Pandas
    # This means all data for THIS BATCH for THIS HOUR goes into one write operation from the driver.
    pandas_df = batch_df.coalesce(1).toPandas()

    if not pandas_df.empty:
        file_exists = os.path.exists(output_file_path)
        # Write header only if file doesn't exist or is empty
        write_header = not file_exists or (file_exists and os.path.getsize(output_file_path) == 0)

        pandas_df.to_csv(output_file_path, mode='a', header=write_header, index=False)
        print(f"Batch {batch_id}: Appended {len(pandas_df)} rows to {output_file_path}. Header written: {write_header}")
    else:
        print(f"Batch {batch_id}: Pandas DataFrame for enriched_df was empty. No data written to {output_file_path}.")

logs_query = enriched_df.writeStream \
    .outputMode("append") \
    .foreachBatch(write_enriched_to_hourly_csv) \
    .trigger(processingTime='1 minute') \
    .option("checkpointLocation", CHECKPOINT_PATH) \
    .start()

alerting_query = aggregated_df.writeStream \
    .foreachBatch(process_agg_and_alerts_batch) \
    .outputMode("update") \
    .trigger(processingTime='1 minute') \
    .option("checkpointLocation", CHECKPOINT_PATH_ALERTS) \
    .start()

spark.streams.awaitAnyTermination()