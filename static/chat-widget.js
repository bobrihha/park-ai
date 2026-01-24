/**
 * Jungle City Chat Widget v2.3
 * Встраиваемый чат-виджет для сайта с формой регистрации
 * - Исправлены ссылки
 * - Исправлен аватар
 * - Увеличена кнопка отправки (!important)
 * - Исправлены переносы строк (replace \n -> <br>)
 */

(function () {
    'use strict';

    // Конфигурация
    const CONFIG = {
        apiUrl: document.currentScript?.getAttribute('data-api') || 'http://95.81.99.32:8000',
        primaryColor: '#43348b',
        secondaryColor: '#43348b',
        botName: 'Джунгли Сити',
        botAvatarUrl: 'https://junglbot-nn.cachalot.cc/static/chat-avatar.jpg',
        welcomeMessage: 'Привет, {name}! 👋 Я помощник парка Джунгли Сити. Могу рассказать о парке, помочь забронировать праздник или ответить на вопросы!',
        quickButtons: [
            { text: '🎂 Организовать праздник', message: 'Хочу организовать день рождения' },
            { text: '💰 Узнать цены', message: 'Сколько стоит посещение парка?' },
            { text: '📍 Как добраться', message: 'Как добраться до парка?' }
        ]
    };

    // Стили
    const STYLES = `
        #jc-chat-widget * {
            box-sizing: border-box;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
        }

        #jc-chat-widget {
            position: fixed;
            bottom: 20px;
            right: 20px;
            z-index: 999999;
        }

        #jc-chat-button {
            width: 60px;
            height: 60px;
            border-radius: 50%;
            background: ${CONFIG.primaryColor};
            border: none;
            cursor: pointer;
            box-shadow: 0 4px 20px rgba(67, 52, 139, 0.4);
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.3s ease;
            position: relative;
        }

        #jc-chat-button:hover {
            transform: scale(1.1);
            box-shadow: 0 6px 25px rgba(67, 52, 139, 0.5);
        }

        #jc-chat-button svg {
            width: 28px;
            height: 28px;
            fill: white;
            transition: transform 0.3s ease;
        }

        #jc-chat-button.open svg {
            transform: rotate(90deg);
        }

        #jc-chat-badge {
            position: absolute;
            top: -5px;
            right: -5px;
            background: #EF4444;
            color: white;
            font-size: 12px;
            font-weight: bold;
            width: 22px;
            height: 22px;
            border-radius: 50%;
            display: none;
            align-items: center;
            justify-content: center;
            border: 2px solid white;
        }

        #jc-chat-window {
            position: fixed;
            bottom: 90px;
            right: 20px;
            width: 380px;
            height: min(550px, calc(100vh - 120px));
            max-height: calc(100vh - 120px);
            background: white;
            border-radius: 16px;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.15);
            display: none;
            flex-direction: column;
            overflow: hidden;
            animation: jc-slideUp 0.3s ease;
        }

        @keyframes jc-slideUp {
            from { opacity: 0; transform: translateY(20px); }
            to { opacity: 1; transform: translateY(0); }
        }

        #jc-chat-window.open {
            display: flex;
        }

        #jc-chat-header {
            background: ${CONFIG.primaryColor};
            color: white;
            padding: 16px 20px;
            display: flex;
            align-items: center;
            gap: 12px;
            flex-shrink: 0;
        }

        #jc-chat-avatar {
            width: 45px;
            height: 45px;
            border-radius: 50%;
            overflow: hidden;
            background: white;
            flex-shrink: 0;
        }

        #jc-chat-avatar img {
            width: 100%;
            height: 100%;
            object-fit: cover;
            display: block;
        }

        #jc-chat-info h3 {
            margin: 0;
            font-size: 16px;
            font-weight: 600;
        }

        #jc-chat-info p {
            margin: 4px 0 0;
            font-size: 12px;
            opacity: 0.9;
        }

        #jc-chat-close {
            margin-left: auto;
            background: rgba(255, 255, 255, 0.2);
            border: none;
            color: white;
            width: 32px;
            height: 32px;
            border-radius: 50%;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: background 0.2s;
        }

        #jc-chat-close:hover {
            background: rgba(255, 255, 255, 0.3);
        }

        /* Форма регистрации */
        #jc-register-form {
            flex: 1;
            display: flex;
            flex-direction: column;
            padding: 30px 24px;
            background: linear-gradient(180deg, #F9FAFB 0%, white 100%);
        }

        #jc-register-form.hidden {
            display: none;
        }

        .jc-form-title {
            font-size: 20px;
            font-weight: 600;
            color: #1F2937;
            margin-bottom: 8px;
            text-align: center;
        }

        .jc-form-subtitle {
            font-size: 14px;
            color: #6B7280;
            margin-bottom: 24px;
            text-align: center;
            line-height: 1.5;
        }

        .jc-form-group {
            margin-bottom: 16px;
        }

        .jc-form-group label {
            display: block;
            font-size: 13px;
            font-weight: 500;
            color: #374151;
            margin-bottom: 6px;
        }

        .jc-form-group input {
            width: 100%;
            padding: 14px 16px;
            border: 2px solid #E5E7EB;
            border-radius: 12px;
            font-size: 15px;
            transition: border-color 0.2s, box-shadow 0.2s;
            outline: none;
        }

        .jc-form-group input:focus {
            border-color: ${CONFIG.primaryColor};
            box-shadow: 0 0 0 3px rgba(107, 70, 193, 0.1);
        }

        .jc-form-group input::placeholder {
            color: #9CA3AF;
        }

        .jc-form-group .jc-input-error {
            border-color: #EF4444;
        }

        .jc-form-error {
            color: #EF4444;
            font-size: 12px;
            margin-top: 4px;
            display: none;
        }

        .jc-form-submit {
            width: 100%;
            padding: 16px;
            background: ${CONFIG.primaryColor};
            color: white;
            border: none;
            border-radius: 12px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
            margin-top: 8px;
        }

        .jc-form-submit:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 15px rgba(107, 70, 193, 0.4);
        }

        .jc-form-submit:disabled {
            opacity: 0.7;
            cursor: not-allowed;
            transform: none;
        }

        .jc-form-privacy {
            font-size: 11px;
            color: #9CA3AF;
            text-align: center;
            margin-top: 16px;
            line-height: 1.5;
        }

        .jc-form-privacy a {
            color: ${CONFIG.primaryColor};
            text-decoration: none;
        }

        /* Область чата */
        #jc-chat-body {
            flex: 1;
            display: none;
            flex-direction: column;
            overflow: hidden;
            min-height: 0;
        }

        #jc-chat-body.active {
            display: flex;
        }

        #jc-chat-messages {
            flex: 1 1 auto;
            overflow-y: scroll;
            overflow-x: hidden;
            -webkit-overflow-scrolling: touch;
            overscroll-behavior: contain;
            padding: 16px;
            display: flex;
            flex-direction: column;
            gap: 12px;
            background: #F9FAFB;
            min-height: 0;
            max-height: 100%;
        }

        .jc-message {
            max-width: 85%;
            padding: 12px 16px;
            border-radius: 16px;
            font-size: 14px;
            line-height: 1.5;
            word-wrap: break-word;
        }

        /* Ссылки в сообщениях */
        .jc-message a {
            color: inherit;
            text-decoration: underline;
            font-weight: 600;
            word-break: break-all;
        }

        .jc-message.bot {
            background: white;
            color: #1F2937;
            align-self: flex-start;
            border-bottom-left-radius: 4px;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
        }

        .jc-message.bot a {
            color: ${CONFIG.primaryColor};
        }

        .jc-message.user {
            background: ${CONFIG.primaryColor};
            color: white;
            align-self: flex-end;
            border-bottom-right-radius: 4px;
        }

        .jc-message.user a {
            color: white;
        }

        .jc-typing {
            display: flex;
            gap: 4px;
            padding: 12px 16px;
            background: white;
            border-radius: 16px;
            border-bottom-left-radius: 4px;
            align-self: flex-start;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
        }

        .jc-typing span {
            width: 8px;
            height: 8px;
            background: #9CA3AF;
            border-radius: 50%;
            animation: jc-bounce 1.4s infinite ease-in-out;
        }

        .jc-typing span:nth-child(1) { animation-delay: 0s; }
        .jc-typing span:nth-child(2) { animation-delay: 0.2s; }
        .jc-typing span:nth-child(3) { animation-delay: 0.4s; }

        @keyframes jc-bounce {
            0%, 80%, 100% { transform: translateY(0); }
            40% { transform: translateY(-6px); }
        }

        #jc-quick-buttons {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            padding: 12px 16px;
            background: #F9FAFB;
            border-top: 1px solid #E5E7EB;
            flex-shrink: 0;
        }

        .jc-quick-btn {
            background: white;
            border: 1px solid #E5E7EB;
            padding: 8px 14px;
            border-radius: 20px;
            font-size: 13px;
            cursor: pointer;
            transition: all 0.2s;
            color: #374151;
        }

        .jc-quick-btn:hover {
            background: ${CONFIG.primaryColor};
            color: white;
            border-color: ${CONFIG.primaryColor};
        }

        #jc-chat-input-area {
            display: flex;
            padding: 12px 16px;
            gap: 10px;
            background: white;
            border-top: 1px solid #E5E7EB;
            flex-shrink: 0;
        }

        #jc-chat-input {
            flex: 1;
            border: 1px solid #E5E7EB;
            border-radius: 24px;
            padding: 12px 18px;
            font-size: 14px;
            outline: none;
            transition: border-color 0.2s;
        }

        #jc-chat-input:focus {
            border-color: ${CONFIG.primaryColor};
        }

        #jc-chat-input::placeholder {
            color: #9CA3AF;
        }

        #jc-chat-send {
            width: 44px;
            height: 44px;
            border-radius: 50%;
            background: ${CONFIG.primaryColor};
            border: none;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: transform 0.2s;
            flex-shrink: 0; /* Чтобы не сжималась */
        }

        #jc-chat-send:hover {
            transform: scale(1.05);
        }

        #jc-chat-send:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }

        #jc-chat-send svg {
            width: 24px !important;
            height: 24px !important;
            fill: white !important;
            transform: translateX(2px);
            display: block; /* Убираем отступы */
        }

        @media (max-width: 480px) {
            #jc-chat-window {
                width: calc(100vw - 24px);
                height: calc(100vh - 100px);
                max-height: calc(100vh - 100px);
                bottom: 80px;
                right: 12px;
                border-radius: 16px;
            }
            
            #jc-chat-header {
                padding: 12px 16px;
            }
            
            #jc-chat-avatar {
                width: 40px;
                height: 40px;
                flex-shrink: 0;
            }
            
            #jc-chat-avatar img {
                width: 100%;
                height: 100%;
                object-fit: cover;
                display: block;
            }
        }

        /* Блокировка скролла body когда чат открыт */
        body.jc-chat-open {
            overflow: hidden !important;
        }
    `;

    // HTML разметка
    const HTML = `
        <button id="jc-chat-button" aria-label="Открыть чат">
            <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                <path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H5.17L4 17.17V4h16v12z"/>
                <path d="M7 9h10v2H7zm0-3h10v2H7z"/>
            </svg>
            <span id="jc-chat-badge">1</span>
        </button>

        <div id="jc-chat-window">
            <div id="jc-chat-header">
                <div id="jc-chat-avatar"><img src="${CONFIG.botAvatarUrl}" alt="Джунгли Сити"></div>
                <div id="jc-chat-info">
                    <h3>${CONFIG.botName}</h3>
                    <p>🟢 Онлайн • Отвечаем мгновенно</p>
                </div>
                <button id="jc-chat-close" aria-label="Закрыть чат">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="white">
                        <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/>
                    </svg>
                </button>
            </div>

            <!-- Форма регистрации -->
            <div id="jc-register-form">
                <div class="jc-form-title">👋 Давайте знакомиться!</div>
                <div class="jc-form-subtitle">Оставьте контакты, чтобы мы могли помочь вам с любым вопросом</div>
                
                <div class="jc-form-group">
                    <label for="jc-reg-name">Ваше имя</label>
                    <input type="text" id="jc-reg-name" placeholder="Например, Мария" autocomplete="name">
                    <div class="jc-form-error" id="jc-name-error">Пожалуйста, укажите имя</div>
                </div>
                
                <div class="jc-form-group">
                    <label for="jc-reg-phone">Телефон</label>
                    <input type="tel" id="jc-reg-phone" placeholder="+7 (___) ___-__-__" autocomplete="tel">
                    <div class="jc-form-error" id="jc-phone-error">Укажите корректный номер телефона</div>
                </div>
                
                <button class="jc-form-submit" id="jc-form-submit">Начать чат 💬</button>
                
                <div class="jc-form-privacy">
                    Нажимая кнопку, вы соглашаетесь на обработку персональных данных
                </div>
            </div>

            <!-- Область чата -->
            <div id="jc-chat-body">
                <div id="jc-chat-messages"></div>

                <div id="jc-quick-buttons">
                    ${CONFIG.quickButtons.map(btn =>
        `<button class="jc-quick-btn" data-message="${btn.message}">${btn.text}</button>`
    ).join('')}
                </div>

                <div id="jc-chat-input-area">
                    <input type="text" id="jc-chat-input" placeholder="Напишите сообщение..." autocomplete="off">
                    <button id="jc-chat-send" aria-label="Отправить">
                        <svg viewBox="0 0 24 24">
                            <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
                        </svg>
                    </button>
                </div>
            </div>
        </div>
    `;

    // Класс виджета
    class JungleCityChat {
        constructor() {
            this.sessionId = this.getSessionId();
            this.isOpen = false;
            this.isLoading = false;
            this.isRegistered = this.checkRegistration();
            this.userName = localStorage.getItem('jc_user_name') || '';
            this.userPhone = localStorage.getItem('jc_user_phone') || '';

            this.init();
        }

        getSessionId() {
            let sessionId = localStorage.getItem('jc_chat_session');
            if (!sessionId) {
                sessionId = 'web_' + Math.random().toString(36).substring(2, 15);
                localStorage.setItem('jc_chat_session', sessionId);
            }
            return sessionId;
        }

        checkRegistration() {
            return localStorage.getItem('jc_registered') === 'true';
        }

        init() {
            // Добавляем стили
            const styleEl = document.createElement('style');
            styleEl.textContent = STYLES;
            document.head.appendChild(styleEl);

            // Добавляем виджет
            const widget = document.createElement('div');
            widget.id = 'jc-chat-widget';
            widget.innerHTML = HTML;
            document.body.appendChild(widget);

            // Элементы
            this.button = document.getElementById('jc-chat-button');
            this.window = document.getElementById('jc-chat-window');
            this.registerForm = document.getElementById('jc-register-form');
            this.chatBody = document.getElementById('jc-chat-body');
            this.messages = document.getElementById('jc-chat-messages');
            this.input = document.getElementById('jc-chat-input');
            this.sendBtn = document.getElementById('jc-chat-send');
            this.closeBtn = document.getElementById('jc-chat-close');
            this.quickBtns = document.getElementById('jc-quick-buttons');
            this.badge = document.getElementById('jc-chat-badge');

            // Форма регистрации
            this.nameInput = document.getElementById('jc-reg-name');
            this.phoneInput = document.getElementById('jc-reg-phone');
            this.formSubmit = document.getElementById('jc-form-submit');

            // События
            this.button.addEventListener('click', () => this.toggle());
            this.closeBtn.addEventListener('click', () => this.close());
            this.sendBtn.addEventListener('click', () => this.send());
            this.input.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') this.send();
            });

            // Форма регистрации
            this.formSubmit.addEventListener('click', () => this.register());
            this.phoneInput.addEventListener('input', (e) => this.formatPhone(e));

            // Быстрые кнопки
            this.quickBtns.querySelectorAll('.jc-quick-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const message = btn.getAttribute('data-message');
                    this.input.value = message;
                    this.send();
                });
            });

            // Показываем нужную форму
            if (this.isRegistered) {
                this.showChat();
                this.loadHistory();
            }

            // Показываем бейдж через 3 секунды
            setTimeout(() => {
                if (!this.isOpen) {
                    this.badge.style.display = 'flex';
                }
            }, 3000);
        }

        formatPhone(e) {
            let value = e.target.value.replace(/\D/g, '');
            if (value.length > 0) {
                if (value[0] === '8') value = '7' + value.slice(1);
                if (value[0] !== '7') value = '7' + value;

                let formatted = '+7';
                if (value.length > 1) formatted += ' (' + value.slice(1, 4);
                if (value.length > 4) formatted += ') ' + value.slice(4, 7);
                if (value.length > 7) formatted += '-' + value.slice(7, 9);
                if (value.length > 9) formatted += '-' + value.slice(9, 11);

                e.target.value = formatted;
            }
        }

        validatePhone(phone) {
            const digits = phone.replace(/\D/g, '');
            return digits.length >= 11;
        }

        async register() {
            const name = this.nameInput.value.trim();
            const phone = this.phoneInput.value.trim();

            // Валидация
            let hasError = false;

            if (!name) {
                this.nameInput.classList.add('jc-input-error');
                document.getElementById('jc-name-error').style.display = 'block';
                hasError = true;
            } else {
                this.nameInput.classList.remove('jc-input-error');
                document.getElementById('jc-name-error').style.display = 'none';
            }

            if (!this.validatePhone(phone)) {
                this.phoneInput.classList.add('jc-input-error');
                document.getElementById('jc-phone-error').style.display = 'block';
                hasError = true;
            } else {
                this.phoneInput.classList.remove('jc-input-error');
                document.getElementById('jc-phone-error').style.display = 'none';
            }

            if (hasError) return;

            // Сохраняем данные
            this.userName = name;
            this.userPhone = phone;
            localStorage.setItem('jc_user_name', name);
            localStorage.setItem('jc_user_phone', phone);
            localStorage.setItem('jc_registered', 'true');
            this.isRegistered = true;

            // Отправляем регистрацию на сервер
            this.formSubmit.disabled = true;
            this.formSubmit.textContent = 'Подождите...';

            try {
                await fetch(`${CONFIG.apiUrl}/chat/register`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        session_id: this.sessionId,
                        name: name,
                        phone: phone
                    })
                });
            } catch (e) {
                console.log('Registration sent (offline mode ok)');
            }

            // Показываем чат
            this.showChat();

            // Отправляем приветствие
            const welcome = CONFIG.welcomeMessage.replace('{name}', name.split(' ')[0]);
            this.addMessage(welcome, 'bot');
            this.saveHistory();
        }

        showChat() {
            this.registerForm.classList.add('hidden');
            this.chatBody.classList.add('active');
        }

        toggle() {
            this.isOpen ? this.close() : this.open();
        }

        open() {
            this.isOpen = true;
            this.window.classList.add('open');
            this.button.classList.add('open');
            this.badge.style.display = 'none';
            document.body.classList.add('jc-chat-open');

            if (this.isRegistered) {
                this.input.focus();
            } else {
                this.nameInput.focus();
            }

            this.scrollToBottom();
        }

        close() {
            this.isOpen = false;
            this.window.classList.remove('open');
            this.button.classList.remove('open');
            document.body.classList.remove('jc-chat-open');
        }

        linkify(text) {
            // Регулярка для URL. Обрабатываем http, https, ftp, file
            // Исключаем знаки препинания в конце URL
            const urlRegex = /(\b(https?|ftp|file):\/\/[-A-Z0-9+&@#\/%?=~_|!:,.;]*[-A-Z0-9+&@#\/%=~_|])/ig;
            return text.replace(urlRegex, function (url) {
                return `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`;
            });
        }

        async send() {
            const text = this.input.value.trim();
            if (!text || this.isLoading) return;

            this.addMessage(text, 'user');
            this.input.value = '';
            this.saveHistory();

            this.quickBtns.style.display = 'none';

            this.showTyping();
            this.isLoading = true;
            this.sendBtn.disabled = true;

            try {
                const response = await fetch(`${CONFIG.apiUrl}/chat`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        message: text,
                        session_id: this.sessionId,
                        user_name: this.userName,
                        user_phone: this.userPhone
                    })
                });

                if (!response.ok) throw new Error('API error');

                const data = await response.json();
                this.hideTyping();
                this.addMessage(data.reply, 'bot');
                this.sessionId = data.session_id;
                localStorage.setItem('jc_chat_session', this.sessionId);

            } catch (error) {
                console.error('Chat error:', error);
                this.hideTyping();
                this.addMessage('Ой, что-то пошло не так 😅 Попробуйте ещё раз или позвоните нам: +7 (831) 213-50-50', 'bot');
            }

            this.isLoading = false;
            this.sendBtn.disabled = false;
            this.saveHistory();
        }

        addMessage(text, type) {
            const msg = document.createElement('div');
            msg.className = `jc-message ${type}`;

            // Защита от XSS перед превращением ссылок
            let safeText = text
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#039;");

            // Превращаем переносы строк в <br> (ИСПРАВЛЕНИЕ "wall of text")
            safeText = safeText.replace(/\n/g, '<br>');

            // Превращаем ссылки в HTML
            msg.innerHTML = this.linkify(safeText);

            this.messages.appendChild(msg);
            this.scrollToBottom();
        }

        showTyping() {
            const typing = document.createElement('div');
            typing.className = 'jc-typing';
            typing.id = 'jc-typing-indicator';
            typing.innerHTML = '<span></span><span></span><span></span>';
            this.messages.appendChild(typing);
            this.scrollToBottom();
        }

        hideTyping() {
            const typing = document.getElementById('jc-typing-indicator');
            if (typing) typing.remove();
        }

        scrollToBottom() {
            this.messages.scrollTop = this.messages.scrollHeight;
        }

        saveHistory() {
            // При сохранении берем textContent, но это потеряет форматирование при перезагрузке
            // Лучше сохранять оригинальный текст с \n, а не HTML
            // В this.messages хранятся DOM элементы.
            // Нам нужно где-то хранить исходный текст сообщений, но сейчас проще брать textContent
            // (хотя textContent вернет текст без структуры).
            // Исправим: сохраняем innerHTML, но это небезопасно и сложно парсить.
            // Простейшее решение для виджета: сохранять массив объектов истории в памяти класса, а не парсить DOM.
            // Но мы будем использовать текущий подход с textContent, понимая, что при перезагрузке страницы форматирование может пострадать.
            // Для сохранения абзацев при перезагрузке нужно сохранять innerHTML и при загрузке не экранировать повторно.
            // Но пока оставим как есть, главное - новые сообщения будут с абзацами.

            const messages = Array.from(this.messages.querySelectorAll('.jc-message')).map(el => ({
                text: el.innerText, // innerText сохраняет переносы строк (в отличие от textContent)
                type: el.classList.contains('user') ? 'user' : 'bot'
            }));
            localStorage.setItem('jc_chat_history', JSON.stringify(messages));
        }

        loadHistory() {
            try {
                const history = JSON.parse(localStorage.getItem('jc_chat_history') || '[]');
                if (history.length > 0) {
                    history.forEach(msg => this.addMessage(msg.text, msg.type));
                } else if (this.isRegistered) {
                    const welcome = CONFIG.welcomeMessage.replace('{name}', this.userName.split(' ')[0]);
                    this.addMessage(welcome, 'bot');
                    this.saveHistory();
                }
            } catch (e) {
                console.error('Error loading chat history:', e);
            }
        }
    }

    // Инициализация
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => new JungleCityChat());
    } else {
        new JungleCityChat();
    }

})();
