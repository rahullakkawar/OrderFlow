# =============================================================
# functions/processor/app.py
# OrderProcessor Lambda — Picks up orders from SQS and
# triggers the Step Functions workflow for processing
#
# Triggered by: SQS Queue (orderflow-queue)
# Responsibilities:
#   1. Receive order message from SQS
#   2. Extract order data
#   3. Start Step Functions workflow execution
#   4. Update order status in DynamoDB
# =============================================================

import json
import os
import sys
import boto3
import logging
from datetime import datetime

# Add shared folder to Python path
sys.path.append(os.path.join(os.path.dirname(__file__), '../../shared'))
from models import Order, OrderStatus

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AWS clients — initialised outside handler for reuse
stepfunctions = boto3.client('stepfunctions')
dynamodb      = boto3.resource('dynamodb')


def lambda_handler(event, context):
    """
    Processes messages from SQS queue.
    Each message contains one order to process.

    Args:
        event:   SQS event containing list of messages
        context: Lambda runtime info
    """
    logger.info(f"OrderProcessor invoked with {len(event['Records'])} message(s)")

    # Track any failed messages
    failed_messages = []

    # Process each message in the batch
    for record in event['Records']:
        try:
            # Extract message body
            message_body = json.loads(record['body'])
            order_id     = message_body['order_id']
            order_data   = message_body['order_data']

            logger.info(f"Processing order: {order_id}")

            # ── Start Step Functions Workflow ──
            execution_arn = start_workflow(order_id, order_data)
            logger.info(f"Step Functions workflow started: {execution_arn}")

            # ── Update order status in DynamoDB ──
            update_order_status(order_id, OrderStatus.VALIDATED)
            logger.info(f"Order status updated to VALIDATED: {order_id}")

        except Exception as e:
            logger.error(f"Failed to process message: {str(e)}", exc_info=True)
            # Add to failed list — SQS will retry these
            failed_messages.append({
                "itemIdentifier": record['messageId']
            })

    # Return failed messages to SQS for retry
    # called "partial batch response"
    # Successful messages are deleted from queue
    # Failed messages are retried automatically
    if failed_messages:
        logger.warning(f"{len(failed_messages)} message(s) failed — will be retried")
        return {"batchItemFailures": failed_messages}

    logger.info("All messages processed successfully")
    return {"batchItemFailures": []}


def start_workflow(order_id: str, order_data: dict) -> str:
    """
    Starts a new Step Functions workflow execution for the order.

    Returns:
        execution_arn: Unique ID of this workflow execution
    """
    state_machine_arn = os.environ.get('STATE_MACHINE_ARN', '')

    # Start execution with order data as input
    response = stepfunctions.start_execution(
        stateMachineArn=state_machine_arn,

        # Unique execution name — order ID makes it traceable
        name=f"orderflow-{order_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",

        # Pass full order data to workflow
        # Step Functions passes this to each Lambda in the workflow
        input=json.dumps({
            "order_id":   order_id,
            "order_data": order_data
        })
    )

    return response['executionArn']


def update_order_status(order_id: str, status: str) -> None:
    """
    Updates the order status in DynamoDB.

    """
    table_name = os.environ.get('ORDERS_TABLE', 'orderflow-orders')
    table      = dynamodb.Table(table_name)

    table.update_item(
        Key={'order_id': order_id},
        UpdateExpression='SET #s = :status, updated_at = :updated_at',
        ExpressionAttributeNames={
            # 'status' is a reserved word in DynamoDB
            # so we use ExpressionAttributeNames to alias it
            '#s': 'status'
        },
        ExpressionAttributeValues={
            ':status':     status,
            ':updated_at': datetime.utcnow().isoformat() + "Z"
        }
    )
