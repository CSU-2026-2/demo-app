"""Совместимый импорт общего Kafka-клиента."""

from common.kafka_client import get_producer, send_todo_created_event

__all__ = ["get_producer", "send_todo_created_event"]
