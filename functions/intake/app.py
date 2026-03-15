# =============================================================
# functions/intake/app.py
# OrderIntake Lambda — Entry point of the OrderFlow pipeline
#
# Triggered by: API Gateway POST /orders
# Responsibilities:
#   1. Receive incoming order request
#   2. Validate basic input fields
#   3. Calculate order total
#   4. Save order to DynamoDB
#   5. Push message to SQS for async processing
#   6. Return order ID to customer immediately
# =============================================================

import json
import os
import sys
import boto3
import logging
from datetime import datetime

# Add shared folder to Python path so we can import models
sys.path.append(os.path.join(os.path.dirname(__file__), '../../shared'))
from models import Order, OrderItem, OrderStatus, build_response

# Set up logging — logs appear in CloudWatch automatically
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AWS clients — initialised outside handler for reuse across invocation
dynamodb = boto3.resource('dynamodb')
sqs      = boto3.client('sqs')


def lambda_handler(event, context):
    """
    Main entry point for OrderIntake Lambda.
    Called by API Gateway when customer places an order.

    Args:
        event:   API Gateway request (headers, body, path params)
        context: Lambda runtime info (function name, timeout etc.)

    Returns:
        API Gateway response with order ID or error message
    """
    logger.info("OrderIntake Lambda invoked")
    logger.info(f"Event: {json.dumps(event)}")

    try:
        # ── Step 1: Parse incoming request body ──
        # API Gateway sends body as a JSON string — we need to parse it
        body = json.loads(event.get('body', '{}'))
        logger.info(f"Parsed request body: {body}")

        # ── Step 2: Basic Input Validation ──
        validation_error = validate_input(body)
        if validation_error:
            logger.warning(f"Input validation failed: {validation_error}")
            return build_response(400, {
                "error": "Validation failed",
                "message": validation_error
            })

        # ── Step 3: Build Order object from request ──
        items = [
            OrderItem(
                product_name=item['product_name'],
                quantity=int(item['quantity']),
                unit_price=float(item['unit_price'])
            )
            for item in body['items']
        ]

        order = Order(
            customer_name=body['customer_name'],
            customer_email=body['customer_email'],
            delivery_address=body['delivery_address'],
            items=items
        )

        # Calculate total amount from all items
        order.calculate_total()

        logger.info(f"Order created: {order.order_id}, Total: {order.total_amount}")

        # ── Step 4: Save to DynamoDB ──
        # This gives us a permanent record of every order
        save_to_dynamodb(order)
        logger.info(f"Order saved to DynamoDB: {order.order_id}")

        # ── Step 5: Push to SQS Queue ──
        # We don't process the order here — we hand it off to
        # the queue for async processing. This means:
        # → Customer gets instant response
        # → Processing happens in background
        # → If processor is busy, order waits safely in queue
        push_to_sqs(order)
        logger.info(f"Order pushed to SQS: {order.order_id}")

        # ── Step 6: Return success response to customer ──
        return build_response(201, {
            "message":  "Order placed successfully!",
            "order_id": order.order_id,
            "status":   order.status,
            "total_amount": order.total_amount,
            "estimated_processing": "Your order is being processed. You will receive an email confirmation shortly."
        })

    except json.JSONDecodeError:
        logger.error("Invalid JSON in request body")
        return build_response(400, {
            "error": "Invalid request",
            "message": "Request body must be valid JSON"
        })

    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        return build_response(500, {
            "error": "Internal server error",
            "message": "An unexpected error occurred. Please try again."
        })


def validate_input(body: dict) -> str | None:
    """
    Validates the incoming order request.
    Returns error message if invalid, None if valid.

    """

    # Customer name is required
    if not body.get('customer_name', '').strip():
        return "customer_name is required"

    # Email is required
    if not body.get('customer_email', '').strip():
        return "customer_email is required"

    # Basic email format check
    if '@' not in body.get('customer_email', ''):
        return "customer_email must be a valid email address"

    # Delivery address is required
    if not body.get('delivery_address', '').strip():
        return "delivery_address is required"

    # Must have at least one item
    items = body.get('items', [])
    if not items:
        return "Order must contain at least one item"

    # Validate each item
    for i, item in enumerate(items):
        if not item.get('product_name', '').strip():
            return f"Item {i+1}: product_name is required"

        if not isinstance(item.get('quantity'), (int, float)) or item['quantity'] <= 0:
            return f"Item {i+1}: quantity must be a positive number"

        if not isinstance(item.get('unit_price'), (int, float)) or item['unit_price'] <= 0:
            return f"Item {i+1}: unit_price must be a positive number"

    return None  # All validations passed!


def save_to_dynamodb(order: Order) -> None:
    """
    Saves the order to DynamoDB table.
    Table name comes from environment variable — not hardcoded!

    """
    table_name = os.environ.get('ORDERS_TABLE', 'orderflow-orders')
    table      = dynamodb.Table(table_name)

    # Convert order to dictionary for DynamoDB storage
    item = order.to_dict()

    # DynamoDB requires Decimal for numbers — convert floats
    item['total_amount'] = str(order.total_amount)

    table.put_item(Item=item)


def push_to_sqs(order: Order) -> None:
    """
    Pushes order to SQS queue for async processing.
    The OrderProcessor Lambda will pick this up.

    """
    queue_url = os.environ.get('ORDER_QUEUE_URL', '')

    sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps({
            "order_id":   order.order_id,
            "order_data": order.to_dict()
        }),
        # Message attributes for filtering/routing
        MessageAttributes={
            'source': {
                'DataType':    'String',
                'StringValue': 'orderflow-intake'
            }
        }
    )