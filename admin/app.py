"""Streamlit Admin Panel — управление базой знаний."""

import sys
from pathlib import Path

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
from datetime import datetime
import os

from db import init_db, SessionLocal, Document, Lead, Session as DBSession, Message, BotCommand, Client, ClientPhone, ClientChild, Prompt
from core.rag import RAGSystem
from core.utils import format_phone

# Инициализация
init_db()

st.set_page_config(
    page_title="Джунгли Сити — Админка",
    page_icon="🐒",
    layout="wide"
)

# Добавляем Lucide иконки через CSS
st.markdown("""
<script src="https://unpkg.com/lucide@latest"></script>
<style>
    /* Lucide иконки для интерфейса */
    .lucide-icon {
        width: 18px;
        height: 18px;
        stroke: currentColor;
        stroke-width: 2;
        stroke-linecap: round;
        stroke-linejoin: round;
        fill: none;
        display: inline-block;
        vertical-align: middle;
        margin-right: 6px;
    }
    .lucide-icon-lg {
        width: 24px;
        height: 24px;
    }
    /* Современный стиль для секций */
    .section-header {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 1rem;
    }
    /* Улучшенные кнопки */
    .stButton button {
        border-radius: 8px !important;
    }
</style>
""", unsafe_allow_html=True)

# Helper функция для иконок в тексте
def icon(name: str, size: str = "sm") -> str:
    """Возвращает SVG иконку Lucide по имени."""
    icons = {
        # Навигация
        "book": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/></svg>',
        "bot": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/><path d="M15 13v2"/><path d="M9 13v2"/></svg>',
        "users": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
        "messages": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
        "target": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>',
        "settings": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>',
        # Действия
        "search": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>',
        "edit": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4Z"/></svg>',
        "save": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>',
        "x": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>',
        "plus": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M5 12h14"/><path d="M12 5v14"/></svg>',
        "trash": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>',
        "check": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg>',
        "refresh": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>',
        # Объекты
        "user": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
        "phone": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg>',
        "calendar": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect width="18" height="18" x="3" y="4" rx="2" ry="2"/><line x1="16" x2="16" y1="2" y2="6"/><line x1="8" x2="8" y1="2" y2="6"/><line x1="3" x2="21" y1="10" y2="10"/></svg>',
        "clock": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
        "clipboard": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect width="8" height="4" x="8" y="2" rx="1" ry="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/></svg>',
        "link": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
        "baby": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M9 12h.01"/><path d="M15 12h.01"/><path d="M10 16c.5.3 1.2.5 2 .5s1.5-.2 2-.5"/><path d="M19 6.3a9 9 0 0 1 1.8 3.9 2 2 0 0 1 0 3.6 9 9 0 0 1-17.6 0 2 2 0 0 1 0-3.6A9 9 0 0 1 12 3c2 0 3.5 1.1 3.5 2.5s-.9 2.5-2 2.5c-.8 0-1.5-.4-1.5-1"/></svg>',
        "gift": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect x="3" y="8" width="18" height="4" rx="1"/><path d="M12 8v13"/><path d="M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7"/><path d="M7.5 8a2.5 2.5 0 0 1 0-5A4.8 8 0 0 1 12 8a4.8 8 0 0 1 4.5-5 2.5 2.5 0 0 1 0 5"/></svg>',
        "file": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/><polyline points="14 2 14 8 20 8"/></svg>',
        "send": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></svg>',
        "eye": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>',
        "brain": '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/><path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"/><path d="M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4"/><path d="M12 18v-5"/></svg>',
    }
    return icons.get(name, "")

# ============ АВТОРИЗАЦИЯ ============
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "jungle2026")

def check_password():
    """Проверка пароля для доступа к админке."""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    
    # Проверка "волшебной ссылки"
    query_params = st.query_params
    auth_token = query_params.get("auth")
    
    if auth_token == ADMIN_PASSWORD:
        st.session_state.authenticated = True
        return True

    if st.session_state.authenticated:
        return True
    
    st.title("Вход в админ-панель")
    st.write("Введите пароль для доступа:")
    
    password = st.text_input("Пароль", type="password", key="password_input")
    
    if st.button("Войти"):
        if password == ADMIN_PASSWORD:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Неверный пароль!")
    
    return False

# Проверяем авторизацию
if not check_password():
    st.stop()

# ============ ОСНОВНОЙ ИНТЕРФЕЙС ============
# Кнопка выхода в сайдбаре
if st.sidebar.button("Выйти"):
    st.session_state.authenticated = False
    st.rerun()



# Сайдбар для навигации
PAGES = ["Заявки", "Клиенты", "Диалоги", "Команды бота", "Промпты", "База знаний", "Настройки"]

if "page_nav" not in st.session_state:
    st.session_state.page_nav = "Заявки"

if "force_page" in st.session_state:
    # Обрабатываем старые эмодзи-названия при переходе
    old_page = st.session_state.force_page
    new_page = old_page.replace("🎯 ", "").replace("👥 ", "").replace("💬 ", "").replace("🤖 ", "").replace("📚 ", "").replace("⚙️ ", "")
    st.session_state.page_nav = new_page
    st.session_state.page_selector = new_page
    del st.session_state.force_page
elif "page_selector" not in st.session_state:
    st.session_state.page_selector = st.session_state.page_nav

def on_page_change():
    st.session_state.page_nav = st.session_state.page_selector

# Минималистичное меню без эмодзи
page = st.sidebar.selectbox(
    "Раздел",
    PAGES,
    key="page_selector",
    index=PAGES.index(st.session_state.page_nav) if st.session_state.page_nav in PAGES else 0,
    on_change=on_page_change
)


# ============ БАЗА ЗНАНИЙ ============
if page == "База знаний":
    st.markdown(f'{icon("book")} <h2 style="display:inline">База знаний</h2>', unsafe_allow_html=True)
    
    tab1, tab2, tab3 = st.tabs(["Документы", "Добавить", "Переиндексация"])
    
    with tab1:
        st.subheader("Существующие документы")
        
        db = SessionLocal()
        docs = db.query(Document).order_by(Document.category, Document.title).all()
        db.close()
        
        if docs:
            for doc in docs:
                with st.expander(f"[{doc.category}] {doc.title}"):
                    st.text_area("Содержимое", doc.content, height=200, key=f"doc_{doc.id}", disabled=True)
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button("Редактировать", key=f"edit_{doc.id}"):
                            st.session_state[f"editing_{doc.id}"] = True
                    with col2:
                        if st.button("Удалить", key=f"del_{doc.id}"):
                            db = SessionLocal()
                            db.query(Document).filter(Document.id == doc.id).delete()
                            db.commit()
                            db.close()
                            st.rerun()
        else:
            st.info("Документы пока не добавлены. Перейдите на вкладку 'Добавить'.")
    
    with tab2:
        st.subheader("Добавить документ")
        
        with st.form("add_document"):
            title = st.text_input("Название документа")
            category = st.selectbox("Категория", ["general", "birthday", "shared"])
            content = st.text_area("Содержимое", height=300)
            
            submitted = st.form_submit_button("Сохранить")
            
            if submitted and title and content:
                db = SessionLocal()
                doc = Document(
                    title=title,
                    category=category,
                    content=content,
                    park_id="nn"
                )
                db.add(doc)
                db.commit()
                db.close()
                
                st.success(f"Документ '{title}' добавлен!")
                st.rerun()
    
    with tab3:
        st.subheader("Обновить афишу с сайта")
        st.write("Загрузить актуальные события с сайта jucity.ru")
        
        if st.button("Загрузить афишу"):
            with st.spinner("Загружаю события с сайта..."):
                try:
                    from core.afisha_scraper import save_afisha_to_knowledge, scrape_afisha
                    content = save_afisha_to_knowledge("nn")
                    st.success("Афиша обновлена!")
                    st.text_area("Загруженные события", content, height=300)
                except Exception as e:
                    st.error(f"Ошибка: {e}")
        
        st.divider()
        st.subheader("Переиндексация RAG")
        st.write("Обновить векторный индекс для поиска по базе знаний.")
        
        if st.button("Переиндексировать"):
            with st.spinner("Индексация..."):
                rag = RAGSystem(park_id="nn")
                rag.clear()
                
                # Индексируем из БД
                db = SessionLocal()
                docs = db.query(Document).all()
                
                for doc in docs:
                    rag.add_document(
                        doc_id=f"db_{doc.id}",
                        content=doc.content,
                        category=doc.category,
                        title=doc.title
                    )
                
                # Плюс файлы из knowledge/
                file_count = rag.index_knowledge_files()
                
                db.close()
                
            st.success(f"Проиндексировано: {len(docs)} документов из БД + {file_count} файлов")


# ============ КОМАНДЫ БОТА ============
elif page == "Команды бота":
    st.markdown(f'{icon("bot")} <h2 style="display:inline">Управление командами бота</h2>', unsafe_allow_html=True)
    
    tab1, tab2 = st.tabs(["Список команд", "Добавить команду"])
    
    with tab1:
        st.subheader("Активные команды")
        
        db = SessionLocal()
        commands = db.query(BotCommand).order_by(BotCommand.order, BotCommand.command).all()
        
        if commands:
            for cmd in commands:
                status_label = "[АКТИВНО]" if cmd.is_active else "[СКРЫТО]"
                
                with st.expander(f"{status_label} /{cmd.command} — {cmd.title}"):
                    with st.form(f"edit_cmd_{cmd.id}"):
                        new_title = st.text_input("Название (в меню)", value=cmd.title)
                        new_response = st.text_area("Ответ (HTML)", value=cmd.response or "", height=150)
                        new_order = st.number_input("Порядок", value=cmd.order, step=1)
                        new_is_active = st.checkbox("Активна", value=cmd.is_active)
                        
                        col1, col2 = st.columns(2)
                        with col1:
                            if st.form_submit_button("Сохранить"):
                                cmd.title = new_title
                                cmd.response = new_response
                                cmd.order = new_order
                                cmd.is_active = new_is_active
                                db.commit()
                                st.success("Обновлено!")
                                st.rerun()
                        
                        with col2:
                            if st.form_submit_button("Удалить"):
                                db.delete(cmd)
                                db.commit()
                                st.warning("Команда удалена!")
                                st.rerun()
        else:
            st.info("Команд пока нет.")
        
        db.close()

    with tab2:
        st.subheader("Новая команда")
        
        with st.form("new_cmd_form"):
            new_command = st.text_input("Команда (без /)", help="Например: prices")
            new_title = st.text_input("Название (в меню)", help="Например: Цены")
            new_response = st.text_area("Ответ (HTML)", help="Текст ответа бота. Поддерживает HTML теги.")
            new_order = st.number_input("Порядок", value=0, step=1)
            
            submitted = st.form_submit_button("Добавить команду")
            
            if submitted:
                if not new_command or not new_title:
                    st.error("Заполните команду и название!")
                else:
                    db = SessionLocal()
                    # Проверка уникальности
                    exists = db.query(BotCommand).filter(BotCommand.command == new_command).first()
                    if exists:
                        st.error("Такая команда уже есть!")
                        db.close()
                    else:
                        cmd = BotCommand(
                            command=new_command,
                            title=new_title,
                            response=new_response,
                            order=new_order,
                            is_active=True,
                            has_logic=False 
                        )
                        db.add(cmd)
                        db.commit()
                        db.close()
                        st.success(f"Команда /{new_command} добавлена!")
                        st.rerun()
            
    st.divider()
    st.info("Примечание: Текст ответов обновляется мгновенно. Для обновления меню может потребоваться перезапуск бота.")


# ============ ПРОМПТЫ ============
elif page == "Промпты":
    st.markdown(f'{icon("brain")} <h2 style="display:inline">Системные промпты AI-агента</h2>', unsafe_allow_html=True)
    
    tab1, tab2 = st.tabs(["Редактирование", "Инициализация"])
    
    with tab1:
        st.subheader("Активные промпты")
        st.caption("Промпты определяют личность и поведение бота в разных сценариях.")
        
        db = SessionLocal()
        prompts = db.query(Prompt).filter(Prompt.park_id == "nn").order_by(Prompt.intent).all()
        
        if prompts:
            for prompt in prompts:
                status_label = "✅" if prompt.is_active else "⚪"
                intent_labels = {
                    "base": "🌟 Базовый",
                    "birthday": "🎂 День рождения",
                    "general": "❓ Общие вопросы",
                    "events": "🎪 Афиша",
                    "clarification": "👋 Приветствие"
                }
                intent_display = intent_labels.get(prompt.intent, prompt.intent)
                
                with st.expander(f"{status_label} {intent_display}: {prompt.name}"):
                    with st.form(f"edit_prompt_{prompt.id}"):
                        new_name = st.text_input("Название", value=prompt.name)
                        new_content = st.text_area("Текст промпта", value=prompt.content, height=400)
                        new_is_active = st.checkbox("Активен", value=prompt.is_active)
                        
                        col1, col2 = st.columns(2)
                        with col1:
                            if st.form_submit_button("💾 Сохранить"):
                                prompt.name = new_name
                                prompt.content = new_content
                                prompt.is_active = new_is_active
                                db.commit()
                                st.success("Промпт обновлён!")
                                st.rerun()
                        
                        with col2:
                            if st.form_submit_button("🗑️ Удалить"):
                                db.delete(prompt)
                                db.commit()
                                st.warning("Промпт удалён!")
                                st.rerun()
        else:
            st.warning("Промпты не найдены. Перейдите на вкладку 'Инициализация' чтобы загрузить промпты из кода.")
        
        db.close()
        
        st.divider()
        st.subheader("➕ Добавить новый промпт")
        
        with st.form("new_prompt_form"):
            new_intent = st.selectbox("Тип промпта", ["base", "birthday", "general", "events", "clarification"])
            new_name = st.text_input("Название", placeholder="Например: Базовый промпт про парк")
            new_content = st.text_area("Текст промпта", height=200)
            
            if st.form_submit_button("Добавить"):
                if new_name and new_content:
                    db = SessionLocal()
                    prompt = Prompt(
                        park_id="nn",
                        intent=new_intent,
                        name=new_name,
                        content=new_content,
                        is_active=True
                    )
                    db.add(prompt)
                    db.commit()
                    db.close()
                    st.success("Промпт добавлен!")
                    st.rerun()
                else:
                    st.error("Заполните название и текст промпта!")
    
    with tab2:
        st.subheader("Загрузить промпты из кода")
        st.caption("Эта функция загрузит текущие промпты из файла config/prompts.py в базу данных для редактирования.")
        
        st.warning("⚠️ Внимание: если промпты уже есть в базе, они будут перезаписаны!")
        
        if st.button("🔄 Инициализировать промпты из кода"):
            try:
                from config.prompts import BASE_SYSTEM_PROMPT, BIRTHDAY_PROMPT, GENERAL_PROMPT, EVENTS_PROMPT, CLARIFICATION_PROMPT
                
                db = SessionLocal()
                
                # Удаляем старые промпты
                db.query(Prompt).filter(Prompt.park_id == "nn").delete()
                
                # Добавляем промпты из кода
                prompts_to_add = [
                    ("base", "Базовый промпт (личность Джуси)", BASE_SYSTEM_PROMPT),
                    ("birthday", "День рождения — полный скрипт продажи", BIRTHDAY_PROMPT),
                    ("general", "Общие вопросы — ответы на вопросы о парке", GENERAL_PROMPT),
                    ("events", "Афиша — информация о мероприятиях", EVENTS_PROMPT),
                    ("clarification", "Приветствие — первое сообщение", CLARIFICATION_PROMPT),
                ]
                
                for intent, name, content in prompts_to_add:
                    prompt = Prompt(
                        park_id="nn",
                        intent=intent,
                        name=name,
                        content=content.strip(),
                        is_active=True
                    )
                    db.add(prompt)
                
                db.commit()
                db.close()
                
                st.success(f"✅ Загружено {len(prompts_to_add)} промптов из кода!")
                st.rerun()
            except Exception as e:
                st.error(f"Ошибка: {e}")


# ============ КЛИЕНТЫ ============
if page == "Клиенты":
    st.markdown(f'{icon("users")} <h2 style="display:inline">База клиентов</h2>', unsafe_allow_html=True)
    
    # Поддерживаем поиск из session_state (для перехода из заявки)
    default_search = st.session_state.pop("client_search", "")
    
    search_query = st.text_input("Поиск клиента (имя, телефон, username, ID)", value=default_search)
    
    db = SessionLocal()
    query = db.query(Client).order_by(Client.total_leads.desc())
    
    if search_query:
        # Если поиск - это число, возможно это ID клиента
        if search_query.isdigit():
            client_by_id = query.filter(Client.id == int(search_query)).first()
            if client_by_id:
                # Нашли по ID - показываем только его
                clients = [client_by_id]
            else:
                # Если не нашли по ID - ищем как обычно
                search = f"%{search_query}%"
                query = query.filter(
                    (Client.customer_name.ilike(search)) |
                    (Client.first_name.ilike(search)) |
                    (Client.last_name.ilike(search)) |
                    (Client.username.ilike(search)) |
                    (Client.phone.ilike(search)) |
                    (Client.telegram_id.ilike(search)) |
                    (Client.vk_id.ilike(search))
                )
                clients = query.limit(50).all()
        else:
            search = f"%{search_query}%"
            query = query.filter(
                (Client.customer_name.ilike(search)) |
                (Client.first_name.ilike(search)) |
                (Client.last_name.ilike(search)) |
                (Client.username.ilike(search)) |
                (Client.phone.ilike(search)) |
                (Client.telegram_id.ilike(search)) |
                (Client.vk_id.ilike(search))
            )
            clients = query.limit(50).all()
    else:
        clients = query.limit(50).all()
    
    
    if clients:
        st.write(f"Найдено клиентов: {len(clients)}")
        for client in clients:
             # Имя для отображения
            display_name = client.customer_name or f"{client.first_name or ''} {client.last_name or ''}".strip()
            if not display_name:
                if client.username:
                    display_name = f"@{client.username}"
                elif client.telegram_id:
                    display_name = f"ID {client.telegram_id}"
                elif client.vk_id:
                    display_name = f"VK {client.vk_id}"
                else:
                    display_name = "Без имени"
            
            display_phone = format_phone(client.phone) or "-"
            vk_id_value = client.vk_id or (client.telegram_id if client.telegram_id and str(client.telegram_id).startswith("vk_") else None)
            tg_id_value = client.telegram_id if client.telegram_id and not str(client.telegram_id).startswith("vk_") else None

            with st.expander(f"{display_name} • {display_phone} • Заявок: {client.total_leads}"):
                # Редактирование клиента
                if st.button("Редактировать", key=f"edit_client_{client.id}"):
                    st.session_state[f"editing_client_{client.id}"] = True
                
                if st.session_state.get(f"editing_client_{client.id}", False):
                    with st.form(key=f"client_edit_form_{client.id}"):
                        st.markdown("### Редактирование данных")
                        
                        col_e1, col_e2 = st.columns(2)
                        with col_e1:
                            edit_customer_name = st.text_input("Имя (из заявки)", value=client.customer_name or "")
                            edit_first_name = st.text_input("Имя (соцсеть)", value=client.first_name or "")
                            edit_last_name = st.text_input("Фамилия (соцсеть)", value=client.last_name or "")
                        with col_e2:
                            edit_username = st.text_input("Username", value=client.username or "")
                            edit_phone = st.text_input("Основной телефон", value=client.phone or "")
                        
                        col_btn1, col_btn2 = st.columns(2)
                        with col_btn1:
                            if st.form_submit_button("Сохранить"):
                                client.customer_name = edit_customer_name.strip() if edit_customer_name else None
                                client.first_name = edit_first_name.strip() if edit_first_name else None
                                client.last_name = edit_last_name.strip() if edit_last_name else None
                                client.username = edit_username.strip() if edit_username else None
                                if edit_phone:
                                    from core.lead_service import normalize_phone
                                    normalized = normalize_phone(edit_phone)
                                    if normalized:
                                        client.phone = normalized
                                
                                db.commit()
                                st.session_state[f"editing_client_{client.id}"] = False
                                st.success("Сохранено!")
                                st.rerun()
                        
                        with col_btn2:
                            if st.form_submit_button("Отмена"):
                                st.session_state[f"editing_client_{client.id}"] = False
                                st.rerun()
                else:
                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown("### Данные клиента")
                        st.write(f"**Telegram ID:** `{tg_id_value}`" if tg_id_value else "**Telegram ID:** -")
                        st.write(f"**VK ID:** `{vk_id_value}`" if vk_id_value else "**VK ID:** -")
                        st.write(f"**Username:** @{client.username}" if client.username else "**Username:** -")
                        st.write(f"**Имя (из заявки):** {client.customer_name or '-'}")
                        social_name = f"{client.first_name or ''} {client.last_name or ''}".strip()
                        st.write(f"**Имя из соцсети:** {social_name or '-'}")
                        st.write(f"**Телефон (осн):** {display_phone}")

                        tg_link = None
                        tg_label = None
                        if client.username:
                            tg_label = f"@{client.username}"
                            tg_link = f"https://t.me/{client.username}"
                        elif tg_id_value:
                            tg_label = f"ID {tg_id_value}"

                        vk_link = None
                        vk_label = None
                        if vk_id_value:
                            vk_id = str(vk_id_value).replace("vk_", "")
                            vk_label = f"id{vk_id}"
                            vk_link = f"https://vk.com/id{vk_id}"

                        if tg_label or vk_link:
                            st.markdown("#### Ссылки")
                            if tg_link:
                                st.markdown(f"- [Telegram: {tg_label}]({tg_link})")
                            elif tg_label:
                                st.markdown(f"- Telegram: {tg_label} (нет ссылки без username)")
                            if vk_link:
                                st.markdown(f"- [VK: {vk_label}]({vk_link})")
                        
                        st.markdown("#### 📜 История телефонов")
                        phones = db.query(ClientPhone).filter(ClientPhone.client_id == client.id).all()
                        if phones:
                            for p in phones:
                                phone_text = format_phone(p.phone) or p.phone or "-"
                                st.write(f"- {phone_text} (был {p.last_used_at.strftime('%d.%m.%Y')})")
                        else:
                            st.write("Нет дополнительных телефонов")
                            
                    with c2:
                        st.markdown("### Дети")
                        children = db.query(ClientChild).filter(ClientChild.client_id == client.id).order_by(ClientChild.name).all()
                        if children:
                            grouped = {}
                            for child in children:
                                entry = grouped.setdefault(child.name, {"dates": [], "ages": set()})
                                if child.event_date and child.event_date not in entry["dates"]:
                                    entry["dates"].append(child.event_date)
                                if child.age:
                                    entry["ages"].add(child.age)

                            for name in sorted(grouped.keys()):
                                entry = grouped[name]
                                ages = ", ".join(str(a) for a in sorted(entry["ages"])) if entry["ages"] else ""
                                ages_text = f" ({ages} лет)" if ages else ""
                                dates_text = ", ".join(entry["dates"]) if entry["dates"] else "-"
                                st.write(f"- **{name}**{ages_text} — даты: {dates_text}")
                        else:
                            st.write("Нет данных о детях")
                            
                        st.markdown("### 📊 Статистика")
                        st.metric("Всего заявок", client.total_leads)
                        
                        if st.button("Переписка", key=f"chat_cl_{client.id}"):
                            st.session_state.filter_user_id = tg_id_value or vk_id_value
                            st.session_state.force_page = "Диалоги"
                            st.rerun()

                    st.divider()
                    st.markdown("### История заявок")
                    client_leads = db.query(Lead).filter(Lead.client_id == client.id).order_by(Lead.created_at.desc()).all()
                    if client_leads:
                        for l in client_leads:
                            status_em = {"new": "🔴", "contacted": "🟡", "booked": "🟢", "cancelled": "⚫"}.get(l.status, "⚪")
                            lead_info = f"{status_em} **{l.event_date or '?'}** — {l.format or '-'} ({l.kids_count or 0} дет.)"
                            if l.customer_name:
                                lead_info += f" — {l.customer_name}"
                            st.write(lead_info)
                    else:
                        st.write("Заявок в истории не найдено.")

    else:
        st.info("Клиенты не найдены.")
    
    db.close()


# ============ ДИАЛОГИ ============
elif page == "Диалоги":
    st.markdown(f'{icon("messages")} <h2 style="display:inline">История диалогов</h2>', unsafe_allow_html=True)
    
    # Фильтр по ID
    filter_id = st.text_input("Поиск по ID (VK или Telegram)", value=st.session_state.get("filter_user_id", ""))
    
    if st.button("Сбросить фильтр"):
        st.session_state.filter_user_id = ""
        st.rerun()
    
    db = SessionLocal()
    
    query = db.query(DBSession).order_by(DBSession.updated_at.desc())
    
    if filter_id:
        # Ищем по telegram_id (частичное совпадение)
        query = query.filter(DBSession.telegram_id.contains(filter_id))
    
    sessions = query.limit(50).all()
    
    if sessions:
        for session in sessions:
            intent_label = "[ПРАЗДНИК]" if session.intent == "birthday" else "[ВОПРОС]" if session.intent == "general" else "[?]"
            # Определяем источник по telegram_id
            source = "VK" if str(session.telegram_id).startswith("vk_") else "Telegram"
            user_id = session.telegram_id.replace("vk_", "") if source == "VK" else session.telegram_id
            
            with st.expander(f"{intent_label} {source}: {user_id} | {session.updated_at.strftime('%d.%m.%Y %H:%M')}"):
                messages = db.query(Message).filter(Message.session_id == session.id).order_by(Message.id).all()
                
                for msg in messages:
                    if msg.role == "user":
                        st.markdown(f"**Пользователь:** {msg.content}")
                    else:
                        st.markdown(f"🐒 **Джулия:** {msg.content}")
                    st.divider()
                
                if session.lead_data:
                    st.json(session.lead_data)
    else:
        st.info("Диалогов не найдено.")
    
    db.close()


# ============ ЗАЯВКИ ============
elif page == "Заявки":
    st.markdown(f'{icon("target")} <h2 style="display:inline">Заявки на праздники</h2>', unsafe_allow_html=True)
    
    db = SessionLocal()
    leads = db.query(Lead).order_by(Lead.created_at.desc()).all()
    
    if leads:
        for lead in leads:
            status_labels = {
                "new": "🔴",
                "contacted": "🟡", 
                "booked": "🟢",
                "cancelled": "⚫"
            }
            status_label = status_labels.get(lead.status, "⚪")
            
            # Определяем источник и создаем ссылку
            source_icon = "📱"
            user_link = None
            if lead.source == "vk" or str(lead.telegram_id).startswith("vk_"):
                vk_id = str(lead.telegram_id).replace("vk_", "")
                user_link = f"https://vk.com/id{vk_id}"
                source_icon = "🔵 VK"
            else:
                if lead.username:
                    user_link = f"https://t.me/{lead.username}"
                    source_icon = "TG"
                else:
                    source_icon = "TG (ID)"

            with st.expander(f"{status_label} {lead.customer_name or 'Без имени'} | {lead.event_date or 'Дата не указана'}"):
                
                # --- БЛОК 1: Основные действия ---
                col_act1, col_act2, col_act3 = st.columns([1, 1, 2])
                with col_act1:
                    if user_link:
                        st.markdown(f"**[{source_icon} Профиль]({user_link})**")
                    else:
                        st.markdown(f"**{source_icon} Telegram ID: {lead.telegram_id}**")
                with col_act2:
                    if st.button("Переписка", key=f"hist_{lead.id}"):
                        st.session_state.filter_user_id = lead.telegram_id
                        st.session_state.force_page = "Диалоги"
                        st.rerun()
                
                st.divider()

                # --- БЛОК 2: Редактирование данных ---
                with st.form(key=f"lead_form_{lead.id}"):
                    c1, c2 = st.columns(2)
                    with c1:
                        new_name = st.text_input("Имя клиента", value=lead.customer_name or "")
                        new_phone = st.text_input("Телефон", value=lead.phone or "")
                        new_child = st.text_input("Имя именинника", value=lead.child_name or "")
                        new_age = st.number_input("Возраст", value=lead.child_age or 0, step=1)
                    
                    with c2:
                        new_date = st.text_input("Дата праздника", value=lead.event_date or "")
                        new_kids = st.number_input("Детей", value=lead.kids_count or 0, step=1)
                        new_adults = st.number_input("Взрослых", value=lead.adults_count or 0, step=1)
                        new_format = st.text_input("Формат", value=lead.format or "")
                    
                    st.markdown("**Дополнительно:**")
                    new_notes = st.text_area("Комментарий / Заметки", value=lead.notes or "", height=100)
                    
                    # Статус внутри формы
                    new_status = st.selectbox(
                        "Статус заявки",
                        ["new", "contacted", "booked", "cancelled"],
                        index=["new", "contacted", "booked", "cancelled"].index(lead.status)
                    )
                    
                    if st.form_submit_button("Сохранить изменения"):
                        lead.customer_name = new_name
                        lead.phone = new_phone
                        lead.child_name = new_child
                        lead.child_age = new_age
                        lead.event_date = new_date
                        lead.kids_count = new_kids
                        lead.adults_count = new_adults
                        lead.format = new_format
                        lead.notes = new_notes
                        lead.status = new_status
                        
                        # СИНХРОНИЗАЦИЯ С КАРТОЧКОЙ КЛИЕНТА
                        if lead.client_id:
                            client = db.query(Client).filter(Client.id == lead.client_id).first()
                            if client:
                                # Обновляем имя клиента
                                if new_name and client.customer_name != new_name:
                                    client.customer_name = new_name
                                
                                # Обновляем телефон
                                if new_phone:
                                    from core.lead_service import normalize_phone
                                    norm_phone = normalize_phone(new_phone)
                                    if norm_phone:
                                        # Обновляем основной, если не был
                                        if not client.phone:
                                            client.phone = norm_phone
                                        # Добавляем в историю если нового
                                        existing_ph = db.query(ClientPhone).filter(
                                            ClientPhone.client_id == client.id,
                                            ClientPhone.phone == norm_phone
                                        ).first()
                                        if not existing_ph:
                                            db.add(ClientPhone(
                                                client_id=client.id,
                                                phone=norm_phone
                                            ))
                                
                                # Обновляем ребенка
                                if new_child:
                                    existing_child = db.query(ClientChild).filter(
                                        ClientChild.client_id == client.id,
                                        ClientChild.name == new_child,
                                        ClientChild.event_date == new_date
                                    ).first()
                                    if not existing_child:
                                        db.add(ClientChild(
                                            client_id=client.id,
                                            name=new_child,
                                            event_date=new_date,
                                            age=new_age
                                        ))
                        
                        db.commit()
                        st.success("Данные обновлены и синхронизированы с карточкой клиента!")
                        st.rerun()
                
                # Ссылка на карточку клиента
                if lead.client_id:
                    if st.button("Открыть карточку клиента", key=f"client_card_{lead.id}"):
                        st.session_state.filter_user_id = ""  # Сброс фильтра
                        st.session_state.force_page = "Клиенты"
                        # Установим поиск по ID клиента
                        st.session_state.client_search = str(lead.client_id)
                        st.rerun()
    else:
        st.info("Заявок пока нет.")
    
    db.close()


# ============ НАСТРОЙКИ ============
elif page == "Настройки":
    st.markdown(f'{icon("settings")} <h2 style="display:inline">Настройки</h2>', unsafe_allow_html=True)
    
    st.subheader("Конфигурация парка")
    st.code("""
PARK_ID: nn
NAME: Джунгли Сити Нижний Новгород
PHONE: +7 (831) 213-50-50
WHATSAPP: +7 (962) 509-74-93
    """)
    
    st.subheader("Статус системы")
    
    # Проверяем подключения
    try:
        db = SessionLocal()
        session_count = db.query(DBSession).count()
        message_count = db.query(Message).count()
        doc_count = db.query(Document).count()
        db.close()
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Сессий", session_count)
        col2.metric("Сообщений", message_count)
        col3.metric("Документов", doc_count)
        
        st.success("✅ База данных подключена")
    except Exception as e:
        st.error(f"Ошибка БД: {e}")
    
    # Проверяем OpenAI
    import os
    if os.getenv("OPENAI_API_KEY"):
        st.success("✅ OpenAI API настроен")
    else:
        st.error("OpenAI API ключ не найден")
    
    # Проверяем Telegram
    if os.getenv("TELEGRAM_BOT_TOKEN"):
        st.success("✅ Telegram Bot Token настроен")
    else:
        st.error("Telegram Bot Token не найден")


# Футер
st.sidebar.divider()
st.sidebar.caption("Джунгли Сити AI Bot v1.0")
