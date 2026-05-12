# API-контракт для дипломного описания

Проект реализован на Django, но эндпоинты спроектированы так же, как REST/FastAPI-контракт: JSON для графиков, комментариев, стадий и уведомлений.

## Графики дашборда

`GET /api/dashboard/charts/`

Параметры:

- `manager_id` — необязательный. Если указан, возвращает портфель конкретного менеджера. Owner может смотреть любого менеджера, менеджер — только себя.

Ответ:

```json
{
  "charts": {
    "labels": ["Май 2024", "Июн 2024"],
    "revenue": [1250000, 980000],
    "active_clients": [42, 39],
    "average_check": [45000, 38000],
    "growth_rate": [0, -21.6]
  }
}
```

## Комментарии к клиенту

`POST /clients/{client_id}/comments/new/`

Тело формы:

- `text` — текст комментария.

`POST /clients/{client_id}/comments/{comment_id}/delete/`

Удалить может Owner или автор комментария.

## Комментарии к заказу

`POST /orders/{order_id}/comments/new/`

Тело формы:

- `text` — текст комментария.

`POST /orders/{order_id}/comments/{comment_id}/delete/`

Удалить может Owner или автор комментария.

## Стадии заказа

`POST /orders/{order_id}/stages/new/`

Тело формы:

- `title` — название новой стадии.

`POST /orders/{order_id}/stages/{stage_id}/toggle/`

Тело формы:

- `is_done=true|false`.

`POST /orders/{order_id}/stages/{stage_id}/delete/`

Удаляет кастомную или стандартную стадию у конкретного заказа.

## Сводка менеджера

`GET /api/managers/{manager_id}/summary/`

Ответ:

```json
{
  "id": 1,
  "full_name": "Горбикова Мария Викторовна",
  "email": "maria@example.com",
  "phone": "+7...",
  "age": 30,
  "clients_count": 227,
  "revenue_total": 12500000,
  "portfolio_url": "/owner/dashboard/manager/1/"
}
```

## Уведомления

`GET /api/notifications/`

Ответ:

```json
{
  "count": 2,
  "items": [
    {
      "id": 10,
      "kind": "client_comment",
      "text": "Посмотреть клиента на этой неделе",
      "url": "/clients/5/#comments",
      "created_at": "12.05.2026 14:30"
    }
  ]
}
```

Уведомление создается, когда Owner оставляет комментарий к клиенту или заказу менеджера. Прочитанным оно становится при открытии карточки клиента или переходе к заказу.
