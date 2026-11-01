import json
import logging
import os
import time
from typing import List

from sqlalchemy.orm import Session

from common.database import SessionLocal
from common.models import TodoItem, TodoStep
from common.kafka_client import create_todo_consumer

from openai import OpenAI
from kafka import TopicPartition
from kafka.structs import OffsetAndMetadata

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

YANDEX_CLOUD_API_KEY = os.getenv("YANDEX_CLOUD_API_KEY")
YANDEX_CLOUD_FOLDER = os.getenv("YANDEX_CLOUD_FOLDER")
client = None


def get_llm_client() -> OpenAI:
    global client
    if not YANDEX_CLOUD_API_KEY or not YANDEX_CLOUD_FOLDER:
        raise RuntimeError("YANDEX_CLOUD_API_KEY and YANDEX_CLOUD_FOLDER are required")
    if client is None:
        client = OpenAI(
            api_key=YANDEX_CLOUD_API_KEY,
            base_url="https://llm.api.cloud.yandex.net/v1",
            project=YANDEX_CLOUD_FOLDER,
            timeout=60,
            max_retries=2,
        )
    return client

def llm_generate_steps(title: str, description: str = "") -> List[str]:
    """
    Генерируем шаги при помощи YandexGPT; ошибки вызова повторяет обработчик.

    Просим модель вернуть ЧИСТЫЙ JSON-массив строк:
    ["step 1", "step 2", ...]
    """
    system_prompt = (
        "Ты помощник, который помогает разбивать цель пользователя на конкретный план действий.\n"
        "Получишь название цели и её описание с пожеланиями пользователя.\n"
        "Учитывай пожелания из обоих полей при составлении плана.\n"
        "Если пользователь указал количество шагов, верни ровно столько шагов.\n"
        "Если количество не указано, верни 3–7 коротких конкретных шагов.\n"
        "Формат ответа — СТРОГО JSON-массив строк, без текста до или после.\n"
        'Пример: ["Сделай X", "Сделай Y", "Сделай Z"]'
    )

    user_prompt = json.dumps(
        {"title": title, "description": description}, ensure_ascii=False
    )

    logger.info("Calling LLM for title: %s", title)

    resp = get_llm_client().chat.completions.create(
        model=f"gpt://{YANDEX_CLOUD_FOLDER}/yandexgpt/rc",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.4,
    )

    content = (resp.choices[0].message.content or "").strip()
    if not content:
        raise ValueError("LLM returned an empty plan")
    logger.debug("LLM raw response: %s", content)

    def try_parse_json(text: str) -> List[str] | None:
        try:
            data = json.loads(text)
            if isinstance(data, list) and data and all(
                isinstance(item, str) and item.strip() for item in data
            ):
                return [item.strip() for item in data]
        except ValueError:
            return None

    steps = try_parse_json(content)
    if steps:
        return steps

    if "```" in content:
        fenced = content.split("```")
        for block in fenced:
            maybe = try_parse_json(block.strip())
            if maybe:
                return maybe
        for block in fenced:
            if "[" in block and "]" in block:
                snippet = block[block.find("[") : block.rfind("]") + 1]
                maybe = try_parse_json(snippet)
                if maybe:
                    return maybe

    if "[" in content and "]" in content:
        snippet = content[content.find("[") : content.rfind("]") + 1]
        steps = try_parse_json(snippet)
        if steps:
            return steps

    raise ValueError("LLM response must contain a nonempty JSON array of step strings")


def process_message(db: Session, todo_id: int, title: str) -> None:
    """Блокировка задачи защищает от одновременной генерации повторных событий."""
    todo: TodoItem | None = (
        db.query(TodoItem).filter(TodoItem.id == todo_id).with_for_update().first()
    )
    if not todo:
        logger.warning("Todo %s not found, skip", todo_id)
        return

    if todo.steps:
        logger.info("Todo %s already has %d steps, skip", todo_id, len(todo.steps))
        return

    # Событие может отстать от редактирования; используем актуальные поля из БД.
    steps_texts = llm_generate_steps(todo.title, todo.description or "")
    logger.info("Generated %d steps for todo %s (%s)", len(steps_texts), todo_id, title)

    for text in steps_texts:
        step = TodoStep(todo_id=todo_id, description=text, completed=False)
        db.add(step)

    db.commit()
    logger.info("Saved %d steps for todo %s", len(steps_texts), todo_id)


def handle_record(consumer, msg) -> None:
    """Подтвердить конкретный offset только после успешной обработки.

    Некорректные внешние сообщения пропускаются с записью в журнал.
    Ошибки БД и LLM выходят наружу: consumer будет пересоздан без commit.
    """
    try:
        value = json.loads(msg.value)
        todo_id = value.get("todo_id") if isinstance(value, dict) else None
        if type(todo_id) is not int or todo_id <= 0:
            raise ValueError("todo_id must be a positive integer")
    except (ValueError, TypeError, UnicodeDecodeError):
        logger.error("Skipping invalid event at %s:%s:%s", msg.topic, msg.partition, msg.offset)
    else:
        with SessionLocal() as db:
            try:
                process_message(db, todo_id=todo_id, title=value.get("title", ""))
            except Exception:
                db.rollback()
                raise
    consumer.commit({
        TopicPartition(msg.topic, msg.partition): OffsetAndMetadata(msg.offset + 1, "", -1)
    })


def main() -> None:
    get_llm_client()
    while True:
        consumer = None
        try:
            consumer = create_todo_consumer(group_id="todo-generator")
            logger.info("Todo generator worker started. Waiting for messages...")
            for msg in consumer:
                handle_record(consumer, msg)
        except Exception:
            logger.exception("Worker failed; uncommitted messages will be retried")
        finally:
            if consumer is not None:
                consumer.close(autocommit=False)
        time.sleep(5)


if __name__ == "__main__":
    main()
