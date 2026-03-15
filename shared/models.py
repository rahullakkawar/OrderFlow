# =============================================================
# shared/models.py
# Shared data models used across all OrderFlow Lambda functions
# =============================================================

from dataclasses import dataclass, field, asdict
from typing import List, Optional
from datetime import datetime
import uuid


@dataclass
class OrderItem:
    """
    Represents a single item in an order.
    Each order can have multiple items.
    """
    product_name: str
    quantity: int
    unit_price: float

    def total_price(self) -> float:
        """Calculate total price for this item"""
        return self.quantity * self.unit_price


@dataclass
class Order:
    """
    Core Order model — used across all Lambda functions.
    Represents the full lifecycle of a customer order.
    """

    # Customer details
    customer_name: str
    customer_email: str
    delivery_address: str

    # Order items
    items: List[OrderItem]

    # Auto-generated fields
    order_id: str = field(
        default_factory=lambda: f"ORD-{datetime.utcnow().strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
    )

    # Financial
    total_amount: float = 0.0

    # Workflow status — updated as order moves through pipeline
    status: str = "PLACED"

    # Timestamps
    created_at: str = field(
        default_factory=lambda: datetime.utcnow().isoformat() + "Z"
    )
    updated_at: Optional[str] = None

    # Populated only if order is rejected
    rejection_reason: Optional[str] = None

    def calculate_total(self) -> float:
        """Calculate and set total amount from all items"""
        self.total_amount = sum(item.total_price() for item in self.items)
        return self.total_amount

    def to_dict(self) -> dict:
        """
        Convert order to dictionary for DynamoDB storage
        and JSON serialization
        """
        return {
            "order_id":        self.order_id,
            "customer_name":   self.customer_name,
            "customer_email":  self.customer_email,
            "delivery_address": self.delivery_address,
            "items": [
                {
                    "product_name": item.product_name,
                    "quantity":     item.quantity,
                    "unit_price":   item.unit_price,
                    "total_price":  item.total_price()
                }
                for item in self.items
            ],
            "total_amount":    self.total_amount,
            "status":          self.status,
            "created_at":      self.created_at,
            "updated_at":      self.updated_at,
            "rejection_reason": self.rejection_reason
        }

    @staticmethod
    def from_dict(data: dict) -> "Order":
        """
        Reconstruct an Order object from a dictionary.
        Used when reading from DynamoDB.
        """
        items = [
            OrderItem(
                product_name=i["product_name"],
                quantity=i["quantity"],
                unit_price=i["unit_price"]
            )
            for i in data.get("items", [])
        ]

        order = Order(
            customer_name=data["customer_name"],
            customer_email=data["customer_email"],
            delivery_address=data["delivery_address"],
            items=items
        )

        # Restore saved fields
        order.order_id        = data["order_id"]
        order.total_amount    = data["total_amount"]
        order.status          = data["status"]
        order.created_at      = data["created_at"]
        order.updated_at      = data.get("updated_at")
        order.rejection_reason = data.get("rejection_reason")

        return order


# ── Workflow Status Constants ──
class OrderStatus:
    """
    All possible workflow stages for an order.
    Use these constants instead of raw strings
    to avoid typos across Lambda functions.
    """
    PLACED     = "PLACED"
    VALIDATED  = "VALIDATED"
    FULFILLED  = "FULFILLED"
    COMPLETED  = "COMPLETED"
    REJECTED   = "REJECTED"


# ── HTTP Response Helper ──
def build_response(status_code: int, body: dict) -> dict:
    """
    Builds a standard API Gateway HTTP response.
    Used by all Lambda functions that respond to API calls.
    """
    import json
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"  # Enable CORS
        },
        "body": json.dumps(body, default=str)
    }
