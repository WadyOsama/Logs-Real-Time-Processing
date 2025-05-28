import random
import uuid
from datetime import datetime, timezone
from time import sleep
from faker import Faker
from confluent_kafka import Producer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.json_schema import JSONSerializer
from confluent_kafka.serialization import SerializationContext, MessageField

# --- Configuration ---
NUM_LOG_LINES = 1000000
fake = Faker('en')

# --- Data Pools for Realism ---
USER_IDS = [f"user_{i:06d}" for i in range(1, 10001)] # 10000 unique users

# --- Log Event Templates ---
# Payment Events (INFO for success, ERROR for failure)
PAYMENT_EVENTS_INFO = [
    {"action": "payment_successful", "level": "INFO", "message_template": "Payment successful for order {order_id}. Amount: ${amount:.2f}. User: {user_id}. Payment ID: {payment_id}. IP: {ip}"},
    {"action": "refund_processed", "level": "INFO", "message_template": "Refund processed for order {order_id}. Amount: ${amount:.2f}. User: {user_id}. Refund ID: {refund_id}. IP: {ip}"},
]
PAYMENT_EVENTS_ERROR = [
    {"action": "payment_failed", "level": "ERROR", "message_template": "Payment FAILED for order {order_id}. Amount: ${amount:.2f}. User: {user_id}. Reason: {reason}. IP: {ip}"},
    {"action": "refund_failed", "level": "ERROR", "message_template": "Refund FAILED for order {order_id}. Amount: ${amount:.2f}. User: {user_id}. Reason: {reason}. IP: {ip}"},
]
PAYMENT_FAILURE_REASONS = ["Insufficient funds", "Card declined", "Expired card", "Invalid CVC", "Payment gateway error", "Transaction blocked by bank"]

JSON_SCHEMA_STR = """
{
    "title": "LogEvent",
    "description": "A log event message",
    "type": "object",
    "properties": {
        "timestamp": {
            "description": "The ISO 8601 timestamp of the log event",
            "type": "string"
        },
        "level": {
            "description": "The log level (e.g., INFO, WARNING, ERROR)",
            "type": "string"
        },
        "message": {
            "description": "The log message content",
            "type": "string"
        }
    },
    "required": ["timestamp", "level", "message"]
}
"""

def generate_log_entry(current_time):
    level = "INFO"
    message_parts = {} # Initialize an empty dictionary

    # Randomly choose between payment success and failure events
    event_list = random.choices(
        [PAYMENT_EVENTS_INFO, PAYMENT_EVENTS_ERROR],
        weights=[0.9, 0.1], # Adjust weights as needed
        k=1
    )[0]

    # Randomly select an event specification from the chosen list
    event_spec = random.choices(
        event_list,
        weights=[0.9,0.1],
        k=1
    )[0]

    # Extract the level from the event specification
    level = event_spec["level"]

    # Generate a unique message for the event
    message_parts = { # Populate for this specific event type
        "order_id": f"order_{uuid.uuid4().hex[:12]}",
        "amount": random.uniform(10.0, 1000.0),
        "user_id": random.choice(USER_IDS),
        "payment_id": f"pay_{uuid.uuid4().hex[:16]}",
        "refund_id": f"ref_{uuid.uuid4().hex[:16]}",
        "reason": random.choice(PAYMENT_FAILURE_REASONS), 
        "ip": fake.ipv4_public()
    }

    # .format will only use keys present in the template string
    message = event_spec["message_template"].format(**message_parts)
    key = event_spec["action"]

    # Create the log entry dictionary
    # Use ISO 8601 format for the timestamp
    return {
        "timestamp": current_time.isoformat(),
        "level": level,
        "message": message
        } \
        ,key


if __name__ == "__main__":

    # --- Schema Registry Client Configuration ---
    schema_registry_conf = {'url': 'http://schema-registry:8081'}
    schema_registry_client = SchemaRegistryClient(schema_registry_conf)

    # --- JSON Serializer for the message value ---
    # The JSONSerializer will automatically register the JSON schema with Schema Registry
    json_serializer = JSONSerializer(
        JSON_SCHEMA_STR,
        schema_registry_client
    )

    # --- Kafka Producer Configuration ---
    producer_conf = {
        'bootstrap.servers': 'kafka1:29092,kafka2:29093,kafka3:29094', 
        'client.id': 'E-Commerce App'
    }

    # Create a Kafka producer instance
    try:
        producer = Producer(producer_conf)
        print("Producer created successfully.")
    except Exception as e:
        print(f"Failed to create producer: {e}")
        exit(1)

    try:
        print("Starting log generation...")
        counter = 1

        # Start generating logs indefinitely
        while True:
            # Poll the producer
            producer.poll(0)

            # Generate a log entry
            current_log_time = datetime.now(timezone.utc)
            log_entry, key = generate_log_entry(current_log_time)

            # Send log entry to Kafka
            producer.produce(topic="payment",
                            key=key,
                            value=json_serializer(log_entry, SerializationContext("payment", MessageField.VALUE))
                            )
            
            # Sleep to simulate real-time log generation
            sleep(random.uniform(0,0.1))

            # Print progress every 1000 messages
            if counter % 1000 == 0:
                print(f"Produced {counter} log entries...")
            counter += 1


    finally:
        print("Flushing producer...")
        # Flush the producer to ensure all messages are sent
        producer.flush(30)
        print("Producer flushed and closed.")