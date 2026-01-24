"""
Сервис для работы с заявками (Lead).
Обеспечивает надёжное сохранение и обновление данных.
"""

import logging
from datetime import datetime
from typing import Optional
from db import SessionLocal, Lead, Client, ClientPhone, ClientChild

logger = logging.getLogger(__name__)


import re

def normalize_phone(phone: str) -> Optional[str]:
    """Нормализация телефона: оставляет только цифры, берет последние 10."""
    if not phone:
        return None
    digits = re.sub(r"\D", "", str(phone))
    if not digits:
        return None
    if len(digits) >= 10:
        return digits[-10:]
    return digits


def normalize_contact_ids(telegram_id: Optional[str], vk_id: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Привести ID в корректный вид (не хранить VK в telegram_id)."""
    if telegram_id and str(telegram_id).startswith("vk_"):
        if not vk_id:
            vk_id = telegram_id
        telegram_id = None
    return telegram_id, vk_id


def client_priority(client: Client) -> tuple:
    """Приоритет клиента при объединении: больше данных -> выше."""
    return (
        1 if client.telegram_id else 0,
        1 if client.vk_id else 0,
        1 if getattr(client, "customer_name", None) else 0,
        1 if client.username else 0,
        1 if (client.first_name or client.last_name) else 0,
        -(client.id or 0),
    )


def get_clients_by_phone(db, norm_phone: str, exclude_client_id: Optional[int] = None) -> list[Client]:
    """Найти всех клиентов по нормализованному телефону."""
    if not norm_phone:
        return []

    client_ids = set()

    phone_rows = db.query(ClientPhone).filter(ClientPhone.phone.contains(norm_phone)).all()
    for row in phone_rows:
        if exclude_client_id and row.client_id == exclude_client_id:
            continue
        client_ids.add(row.client_id)

    client_rows = db.query(Client).filter(Client.phone.contains(norm_phone)).all()
    for row in client_rows:
        if exclude_client_id and row.id == exclude_client_id:
            continue
        client_ids.add(row.id)

    if not client_ids:
        phone_rows = db.query(ClientPhone).all()
        for row in phone_rows:
            if normalize_phone(row.phone) == norm_phone:
                if exclude_client_id and row.client_id == exclude_client_id:
                    continue
                client_ids.add(row.client_id)

        client_rows = db.query(Client).filter(Client.phone.isnot(None)).all()
        for row in client_rows:
            if normalize_phone(row.phone) == norm_phone:
                if exclude_client_id and row.id == exclude_client_id:
                    continue
                client_ids.add(row.id)

    if not client_ids:
        return []

    return db.query(Client).filter(Client.id.in_(client_ids)).all()


def merge_clients(db, master: Client, dup: Client) -> Client:
    """Объединить два клиента, сохранив master."""
    if master.id == dup.id:
        return master

    master_tg, master_vk = normalize_contact_ids(master.telegram_id, master.vk_id)
    if master.telegram_id != master_tg:
        master.telegram_id = master_tg
    if master.vk_id != master_vk:
        master.vk_id = master_vk

    dup_tg, dup_vk = normalize_contact_ids(dup.telegram_id, dup.vk_id)

    # Переносим связи
    db.query(Lead).filter(Lead.client_id == dup.id).update(
        {Lead.client_id: master.id}, synchronize_session=False
    )
    db.query(ClientChild).filter(ClientChild.client_id == dup.id).update(
        {ClientChild.client_id: master.id}, synchronize_session=False
    )
    db.query(ClientPhone).filter(ClientPhone.client_id == dup.id).update(
        {ClientPhone.client_id: master.id}, synchronize_session=False
    )

    # Объединяем данные
    if not master.telegram_id and dup_tg:
        master.telegram_id = dup_tg
    if not master.vk_id and dup_vk:
        master.vk_id = dup_vk
    if not master.username and dup.username:
        master.username = dup.username
    if not master.customer_name and getattr(dup, "customer_name", None):
        master.customer_name = dup.customer_name
    if not master.first_name and dup.first_name:
        master.first_name = dup.first_name
    if not master.last_name and dup.last_name:
        master.last_name = dup.last_name
    if not master.phone and dup.phone:
        master.phone = dup.phone
    if dup.phone:
        existing = db.query(ClientPhone).filter(
            ClientPhone.client_id == master.id,
            ClientPhone.phone == dup.phone
        ).first()
        if not existing:
            db.add(ClientPhone(client_id=master.id, phone=dup.phone))

    master.updated_at = datetime.utcnow()

    # Обновляем счетчик
    master.total_leads = db.query(Lead).filter(Lead.client_id == master.id).count()

    db.delete(dup)
    return master


def ensure_client(db, telegram_id: str = None, vk_id: str = None, username: str = None, phone: str = None, first_name: str = None, last_name: str = None) -> Client:
    """Гарантировать существование клиента. Ищет по ID или телефону. Объединяет если находит."""
    client = None
    telegram_id, vk_id = normalize_contact_ids(telegram_id, vk_id)
    stored_phone = normalize_phone(phone) if phone else None
    
    # 1. Поиск по ID
    if telegram_id:
        client = db.query(Client).filter(Client.telegram_id == str(telegram_id)).first()
    if not client and vk_id:
        client = db.query(Client).filter(Client.vk_id == str(vk_id)).first()
        
    # 2. Поиск по телефону (если не нашли по ID, но есть телефон)
    if not client and stored_phone:
        phone_matches = get_clients_by_phone(db, stored_phone)
        if phone_matches:
            phone_matches.sort(key=client_priority, reverse=True)
            client = phone_matches[0]
    
    # 3. Создание или обновление
    if not client:
        # Создаем нового
        client = Client(
            telegram_id=str(telegram_id) if telegram_id else None,
            vk_id=str(vk_id) if vk_id else None,
            username=username,
            first_name=first_name,
            last_name=last_name,
            phone=stored_phone
        )
        db.add(client)
        db.commit()
        db.refresh(client)
        if stored_phone:
            # Сразу добавляем телефон в историю
            db.add(ClientPhone(client_id=client.id, phone=stored_phone))
            db.commit()
            
        logger.info(f"Created new Client #{client.id} (tg={telegram_id}, vk={vk_id})")
    else:
        # 4. ОБЪЕДИНЕНИЕ (MERGE) / Обновление данных
        changed = False
        
        # Если нашли клиента, но у него нет текущего ID (например, нашли по телефону, а теперь пишем с ТГ)
        if telegram_id and not client.telegram_id:
            client.telegram_id = str(telegram_id)
            changed = True
            logger.info(f"Merged Client #{client.id}: added telegram_id {telegram_id}")
            
        if vk_id and not client.vk_id:
            client.vk_id = str(vk_id)
            changed = True
            logger.info(f"Merged Client #{client.id}: added vk_id {vk_id}")

        # Обновляем инфо
        if username and client.username != username:
            client.username = username
            changed = True
        if first_name and not client.first_name:
            client.first_name = first_name
            changed = True
        if last_name and not client.last_name:
            client.last_name = last_name
            changed = True
        if stored_phone:
            # Если телефон новый -> добавляем в историю
            if client.phone != stored_phone:
                # Проверяем нет ли его уже
                existing = db.query(ClientPhone).filter(
                    ClientPhone.client_id == client.id,
                    ClientPhone.phone == stored_phone
                ).first()
                if not existing:
                    db.add(ClientPhone(client_id=client.id, phone=stored_phone))
                    db.commit() # Сохраняем телефон отдельно
                
            if not client.phone:
                client.phone = stored_phone
                changed = True
            
        if changed:
            client.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(client)
            
    return client


def get_or_create_lead(user_id: str, source: str = "telegram", park_id: str = "nn", username: str = None, first_name: str = None, last_name: str = None) -> Lead:
    """Получить существующий или создать новый лид."""
    db = SessionLocal()
    
    # Определяем ID
    tg_id = user_id if source == "telegram" else None
    vk_uid = user_id if source == "vk" else None
    
    # 1. Гарантируем клиента
    client = ensure_client(db, telegram_id=tg_id, vk_id=vk_uid, username=username, first_name=first_name, last_name=last_name)
    
    # Ищем активный лид:
    # - ИЛИ ещё не отправлен менеджеру (sent_to_manager == False)
    # - ИЛИ уже в AmoCRM но ещё активный (есть amocrm_deal_id)
    from sqlalchemy import or_
    lead = db.query(Lead).filter(
        Lead.telegram_id == str(user_id),
        Lead.park_id == park_id,
        Lead.status.in_(["new", "contacted"]),
        or_(
            Lead.sent_to_manager == False,  # Ещё не отправлен
            Lead.amocrm_deal_id != None  # Или уже в CRM (продолжаем обновлять)
        )
    ).order_by(Lead.id.desc()).first()  # Берём последний
    
    if not lead:
        lead = Lead(
            telegram_id=str(user_id),
            park_id=park_id,
            source=source,
            username=username,
            status="new",
            client_id=client.id  # Связываем с клиентом
        )
        db.add(lead)
        # Увеличиваем счетчик лидов у клиента
        client.total_leads += 1
        db.commit()
        db.refresh(lead)
    else:
        # Если лид есть, но без client_id (старый) — привязываем
        if not lead.client_id:
            lead.client_id = client.id
            db.commit()
            
        # Обновляем username если появился
        if username and lead.username != username:
            lead.username = username
            db.commit()
            db.refresh(lead)
            
    db.close()
    return lead


def update_lead_from_data(lead_id: int, data: dict) -> Lead:
    """
    Обновить лид данными из словаря.
    Обновляет только непустые поля.
    """
    db = SessionLocal()
    try:
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if not lead:
            logger.error(f"Lead #{lead_id} not found!")
            return None

        raw_phone = data.get("phone")
        if raw_phone:
            normalized_phone = normalize_phone(raw_phone)
            if normalized_phone:
                data["phone"] = normalized_phone

        customer_name = data.get("customer_name")
        if customer_name:
            data["customer_name"] = customer_name.strip()
        
        # Маппинг полей
        field_mapping = {
            "customer_name": "customer_name",
            "phone": "phone",
            "child_name": "child_name",
            "child_age": "child_age",
            "event_date": "event_date",
            "time": "time",
            "room": "room",
            "kids_count": "kids_count",
            "adults_count": "adults_count",
            "format": "format",
            "extras": "extras",
        }
        
        updated_fields = []
        for data_key, model_key in field_mapping.items():
            value = data.get(data_key)
            if value is not None and value != "" and value != []:
                # ИСПРАВЛЕНИЕ: extras - это список, конвертируем в JSON для SQLite
                if model_key == "extras" and isinstance(value, list):
                    import json
                    value = json.dumps(value, ensure_ascii=False)
                
                current_value = getattr(lead, model_key, None)
                if current_value != value:
                    setattr(lead, model_key, value)
                    updated_fields.append(f"{model_key}={value}")
        
        if updated_fields:
            lead.updated_at = datetime.utcnow()
            
            # --- CRM LOGIC: Обновляем данные клиента ---
            if lead.client_id:
                client = db.query(Client).filter(Client.id == lead.client_id).first()
                if client:
                    # 0. Обновляем имя клиента, если оно указано в заявке
                    new_customer_name = data.get("customer_name")
                    if new_customer_name and client.customer_name != new_customer_name:
                        client.customer_name = new_customer_name

                    # 1. Если обновился телефон
                    new_phone = data.get("phone")
                    if new_phone:
                        # Нормализуем существующий основной телефон
                        if client.phone:
                            client_phone_norm = normalize_phone(client.phone)
                            if client_phone_norm and client.phone != client_phone_norm:
                                client.phone = client_phone_norm

                        # Обновляем основной телефон если не был задан
                        if not client.phone:
                            client.phone = new_phone
                            db.add(client)
                        
                        # Добавляем в историю телефонов
                        # Проверяем нет ли уже такого телефона у этого клиента
                        existing_phone = db.query(ClientPhone).filter(
                            ClientPhone.client_id == client.id, 
                            ClientPhone.phone == new_phone
                        ).first()
                        
                        if not existing_phone:
                            db.add(ClientPhone(client_id=client.id, phone=new_phone))
                        else:
                            # Обновляем дату использования
                            existing_phone.last_used_at = datetime.utcnow()

                        # 1.1 Онлайн-объединение по телефону
                        norm_phone = normalize_phone(new_phone)
                        if norm_phone:
                            matches = get_clients_by_phone(db, norm_phone, exclude_client_id=client.id)
                            for other in matches:
                                master = client
                                dup = other
                                if client_priority(other) > client_priority(client):
                                    master, dup = other, client
                                master = merge_clients(db, master, dup)
                                client = master
                                if lead.client_id != master.id:
                                    lead.client_id = master.id

                    # 2. Если обновился ребенок
                    new_child = data.get("child_name")
                    new_age = data.get("child_age")
                    new_event_date = data.get("event_date")
                    if new_child:
                        # Проверяем есть ли такой ребенок с той же датой
                        child_query = db.query(ClientChild).filter(
                             ClientChild.client_id == client.id,
                             ClientChild.name == new_child
                        )
                        if new_event_date:
                            child_query = child_query.filter(ClientChild.event_date == new_event_date)
                        else:
                            child_query = child_query.filter(ClientChild.event_date.is_(None))

                        existing_child = child_query.first()

                        # Если даты нет в записи, но она появилась — обновим её
                        if not existing_child and new_event_date:
                            existing_child = db.query(ClientChild).filter(
                                ClientChild.client_id == client.id,
                                ClientChild.name == new_child,
                                ClientChild.event_date.is_(None)
                            ).first()
                            if existing_child:
                                existing_child.event_date = new_event_date
                        
                        if not existing_child:
                            db.add(ClientChild(client_id=client.id, name=new_child, event_date=new_event_date, age=new_age))
                        elif new_age:
                            # Обновляем возраст если изменился
                            existing_child.age = new_age
            
            db.commit()
            logger.info(f"Updated lead #{lead.id}: {', '.join(updated_fields)}")
        
        db.refresh(lead)
        return lead
    finally:
        db.close()


def mark_lead_sent_to_manager(lead_id: int) -> bool:
    """Пометить лид как отправленный менеджеру."""
    db = SessionLocal()
    try:
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if lead:
            lead.sent_to_manager = True
            lead.updated_at = datetime.utcnow()
            db.commit()
            logger.info(f"Lead #{lead.id} marked as sent to manager")
            return True
        return False
    finally:
        db.close()


def save_amocrm_deal_id(lead_id: int, deal_id: str) -> bool:
    """Сохранить ID сделки AmoCRM в лиде."""
    db = SessionLocal()
    try:
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if lead:
            lead.amocrm_deal_id = deal_id
            lead.updated_at = datetime.utcnow()
            db.commit()
            logger.info(f"Lead #{lead.id} saved amocrm_deal_id={deal_id}")
            return True
        return False
    finally:
        db.close()


def save_amocrm_contact_id(lead_id: int, contact_id: str) -> bool:
    """Сохранить ID контакта AmoCRM в лиде."""
    db = SessionLocal()
    try:
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if lead:
            lead.amocrm_contact_id = contact_id
            lead.updated_at = datetime.utcnow()
            db.commit()
            logger.info(f"Lead #{lead.id} saved amocrm_contact_id={contact_id}")
            return True
        return False
    finally:
        db.close()


def mark_status_notified(lead_id: int) -> bool:
    """Пометить что клиент уведомлён о смене статуса на 'Взято в работу'."""
    db = SessionLocal()
    try:
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if lead:
            lead.status_notified = True
            lead.updated_at = datetime.utcnow()
            db.commit()
            logger.info(f"Lead #{lead.id} marked as status_notified")
            return True
        return False
    finally:
        db.close()


def get_active_lead_info(user_id: int, park_id: str = "nn") -> Optional[dict]:
    """
    Получить информацию об активной заявке пользователя.
    Возвращает None если нет активной заявки.
    """
    db = SessionLocal()
    try:
        from sqlalchemy import or_
        lead = db.query(Lead).filter(
            Lead.telegram_id == str(user_id),
            Lead.park_id == park_id,
            Lead.status.in_(["new", "contacted"]),
            or_(
                Lead.sent_to_manager == False,
                Lead.amocrm_deal_id != None
            )
        ).order_by(Lead.id.desc()).first()
        
        if lead and lead.event_date:
            return {
                "lead_id": lead.id,
                "event_date": lead.event_date,
                "phone": lead.phone,
                "customer_name": lead.customer_name,
                "amocrm_deal_id": lead.amocrm_deal_id
            }
        return None
    finally:
        db.close()


def get_last_known_phone(user_id: int) -> Optional[str]:
    """
    Найти последний известный телефон пользователя из ЛЮБОЙ заявки.
    Полезно когда создаём новую бронь для существующего клиента.
    """
    db = SessionLocal()
    try:
        # Ищем любую заявку с телефоном
        lead = db.query(Lead).filter(
            Lead.telegram_id == str(user_id),
            Lead.phone != None,
            Lead.phone != ""
        ).order_by(Lead.id.desc()).first()
        
        if lead and lead.phone:
            logger.info(f"Found last known phone for user {user_id}: {lead.phone} from Lead #{lead.id}")
            return lead.phone
        return None
    finally:
        db.close()


def force_create_new_lead(user_id: int, park_id: str = "nn", username: str = None, source: str = "telegram") -> Lead:
    """
    Принудительно создать новую заявку (когда юзер выбрал 'новая бронь').
    Помечает старую заявку как completed и создаёт новую.
    """
    db = SessionLocal()
    try:
        from sqlalchemy import or_
        # Помечаем старые активные заявки как completed
        old_leads = db.query(Lead).filter(
            Lead.telegram_id == str(user_id),
            Lead.park_id == park_id,
            Lead.status.in_(["new", "contacted"])
        ).all()
        
        for old_lead in old_leads:
            old_lead.status = "completed"
            logger.info(f"Marked old Lead #{old_lead.id} as completed")
        
        # Создаём новую заявку
        new_lead = Lead(
            telegram_id=str(user_id),
            park_id=park_id,
            source=source,
            username=username,
            status="new"
        )
        db.add(new_lead)
        db.commit()
        db.refresh(new_lead)
        logger.info(f"Created new Lead #{new_lead.id} for user {user_id}")
        return new_lead
    finally:
        db.close()


def lead_to_dict(lead: Lead) -> dict:
    """Преобразовать Lead в словарь для уведомлений."""
    return {
        "customer_name": lead.customer_name,
        "phone": lead.phone,
        "child_name": lead.child_name,
        "child_age": lead.child_age,
        "event_date": lead.event_date,
        "time": lead.time,
        "room": lead.room,
        "kids_count": lead.kids_count,
        "adults_count": lead.adults_count,
        "format": lead.format,
        "extras": lead.extras or [],
    }


def get_lead_by_id(lead_id: int) -> Optional[Lead]:
    """Получить лид по ID."""
    db = SessionLocal()
    try:
        return db.query(Lead).filter(Lead.id == lead_id).first()
    finally:
        db.close()
