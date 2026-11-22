import json
import os
import uuid

import boto3
from flask import Flask, jsonify, request

app = Flask(__name__)
sns = boto3.client("sns")
TOPIC_ARN = os.environ["ORDER_TOPIC_ARN"]


@app.route("/health")
def health():
    # Plain 200, no dependencies checked - this is what the ALB target group polls.
    return "ok", 200


@app.route("/orders", methods=["POST"])
def create_order():
    """
    The ECS service's one real job for SIS3: accept an order and publish a
    domain event. It does not process the order itself - that is the
    consumer Lambda's job (see infra/01-foundation.yaml), decoupled via SNS -> SQS.
    """
    order_id = str(uuid.uuid4())
    payload = request.get_json(silent=True) or {}
    payload["orderId"] = order_id

    sns.publish(TopicArn=TOPIC_ARN, Message=json.dumps(payload))

    return jsonify({"orderId": order_id, "status": "accepted"}), 202


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
