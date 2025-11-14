/**
 * UI приложение для работы с CAdES Plugin
 */

let plugin = null;
let currentSignature = null;

// ==================== Инициализация ====================

function initializePlugin() {
    const apiUrl = document.getElementById('api-url').value;
    const authToken = document.getElementById('auth-token').value || null;

    try {
        plugin = new CAdESPlugin(apiUrl, authToken);

        // Проверяем соединение загрузкой списка сертификатов
        plugin.listCertificates()
            .then(result => {
                showStatus('connection-status', 'success', `✓ Подключено. Найдено сертификатов: ${result.count}`);

                // Показываем секции
                document.getElementById('certs-section').style.display = 'block';
                document.getElementById('sign-section').style.display = 'block';
                document.getElementById('verify-section').style.display = 'block';
                document.getElementById('stats-section').style.display = 'block';

                // Загружаем данные
                loadCertificates();
                loadStatistics();
            })
            .catch(error => {
                showStatus('connection-status', 'error', `✗ Ошибка подключения: ${error.message}`);
                console.error('Connection error:', error);
            });
    } catch (error) {
        showStatus('connection-status', 'error', `✗ Ошибка: ${error.message}`);
        console.error('Initialization error:', error);
    }
}

// ==================== Работа с сертификатами ====================

async function importCertificate() {
    const fileInput = document.getElementById('pfx-file');
    const password = document.getElementById('pfx-password').value;

    if (!fileInput.files[0]) {
        alert('Выберите файл PKCS#12');
        return;
    }

    if (!password) {
        alert('Введите пароль');
        return;
    }

    try {
        showLoading('Импорт сертификата...');

        const result = await plugin.importCertificate(fileInput.files[0], password);

        hideLoading();

        if (result.success) {
            alert(`Сертификат импортирован:\n${result.certificate.subject_cn}`);

            // Очищаем форму
            fileInput.value = '';
            document.getElementById('pfx-password').value = '';

            // Обновляем список
            loadCertificates();
        } else {
            alert(`Ошибка импорта: ${result.error}`);
        }
    } catch (error) {
        hideLoading();
        alert(`Ошибка: ${error.message}`);
        console.error('Import error:', error);
    }
}

async function loadCertificates() {
    try {
        showLoading('Загрузка сертификатов...');

        const result = await plugin.listCertificates();

        hideLoading();

        if (result.success) {
            displayCertificates(result.certificates);
            updateCertificateSelects(result.certificates);
        } else {
            alert(`Ошибка загрузки: ${result.error}`);
        }
    } catch (error) {
        hideLoading();
        alert(`Ошибка: ${error.message}`);
        console.error('Load certificates error:', error);
    }
}

function displayCertificates(certificates) {
    const container = document.getElementById('certs-list');

    if (certificates.length === 0) {
        container.innerHTML = '<p class="no-data">Нет сертификатов</p>';
        return;
    }

    container.innerHTML = certificates.map(cert => {
        const isValid = plugin.isCertificateValid(cert);
        const statusClass = isValid ? 'cert-valid' : 'cert-invalid';
        const statusText = isValid ? '✓ Действителен' : '✗ Недействителен';

        return `
            <div class="cert-card ${statusClass}">
                <div class="cert-header">
                    <strong>${cert.subject_cn}</strong>
                    <span class="cert-status">${statusText}</span>
                </div>
                <div class="cert-body">
                    <p><strong>Издатель:</strong> ${cert.issuer_cn}</p>
                    <p><strong>Отпечаток:</strong> <code>${cert.thumbprint}</code></p>
                    <p><strong>Действителен с:</strong> ${plugin.formatDate(cert.valid_from)}</p>
                    <p><strong>Действителен до:</strong> ${plugin.formatDate(cert.valid_to)}</p>
                    <p><strong>Закрытый ключ:</strong> ${cert.has_private_key ? 'Да ✓' : 'Нет'}</p>
                </div>
                <div class="cert-footer">
                    <button onclick="deleteCert('${cert.thumbprint}')" class="btn btn-danger btn-sm">Удалить</button>
                    <button onclick="showCertDetails('${cert.thumbprint}')" class="btn btn-secondary btn-sm">Подробнее</button>
                </div>
            </div>
        `;
    }).join('');
}

function updateCertificateSelects(certificates) {
    const select = document.getElementById('sign-cert-select');

    select.innerHTML = '<option value="">-- Выберите сертификат --</option>' +
        certificates.map(cert => {
            const isValid = plugin.isCertificateValid(cert);
            const status = isValid ? '' : ' (недействителен)';
            return `<option value="${cert.thumbprint}">${cert.subject_cn}${status}</option>`;
        }).join('');
}

async function deleteCert(thumbprint) {
    if (!confirm('Вы уверены, что хотите удалить сертификат?')) {
        return;
    }

    try {
        showLoading('Удаление сертификата...');

        const result = await plugin.deleteCertificate(thumbprint);

        hideLoading();

        if (result.success) {
            alert('Сертификат удален');
            loadCertificates();
        } else {
            alert(`Ошибка удаления: ${result.error}`);
        }
    } catch (error) {
        hideLoading();
        alert(`Ошибка: ${error.message}`);
        console.error('Delete certificate error:', error);
    }
}

async function showCertDetails(thumbprint) {
    try {
        const result = await plugin.getCertificate(thumbprint);

        if (result.success) {
            const info = plugin.getCertificateInfo(result.certificate);
            alert(info);
        } else {
            alert(`Ошибка: ${result.error}`);
        }
    } catch (error) {
        alert(`Ошибка: ${error.message}`);
        console.error('Get certificate error:', error);
    }
}

// ==================== Подпись данных ====================

async function signData() {
    const thumbprint = document.getElementById('sign-cert-select').value;
    const password = document.getElementById('sign-password').value;
    const detached = document.getElementById('sign-detached').checked;

    if (!thumbprint) {
        alert('Выберите сертификат');
        return;
    }

    if (!password) {
        alert('Введите пароль');
        return;
    }

    // Получаем данные
    const dataType = document.querySelector('input[name="sign-data-type"]:checked').value;
    let data;

    if (dataType === 'text') {
        data = document.getElementById('sign-text').value;
        if (!data) {
            alert('Введите текст');
            return;
        }
    } else {
        const fileInput = document.getElementById('sign-file');
        if (!fileInput.files[0]) {
            alert('Выберите файл');
            return;
        }
        data = fileInput.files[0];
    }

    try {
        showLoading('Создание подписи...');

        const result = await plugin.signData(data, thumbprint, password, detached);

        hideLoading();

        if (result.success) {
            currentSignature = result.signature;

            document.getElementById('sign-result').style.display = 'block';
            document.getElementById('sign-result-content').innerHTML = `
                <p><strong>Тип подписи:</strong> ${result.signature_type}</p>
                <p><strong>Подписчик:</strong> ${result.signer}</p>
                <p><strong>Длина подписи:</strong> ${result.signature.length} символов</p>
                <div class="signature-preview">
                    <code>${result.signature.substring(0, 100)}...</code>
                </div>
            `;

            alert('Подпись создана успешно!');
        } else {
            alert(`Ошибка создания подписи: ${result.error}`);
        }
    } catch (error) {
        hideLoading();
        alert(`Ошибка: ${error.message}`);
        console.error('Sign data error:', error);
    }
}

function downloadSignature() {
    if (!currentSignature) {
        alert('Нет подписи для скачивания');
        return;
    }

    plugin.downloadSignature(currentSignature, 'signature.sig');
}

// ==================== Проверка подписи ====================

async function verifySignature() {
    // Получаем данные
    const dataType = document.querySelector('input[name="verify-data-type"]:checked').value;
    let data;

    if (dataType === 'text') {
        data = document.getElementById('verify-text').value;
        if (!data) {
            alert('Введите текст');
            return;
        }
    } else {
        const fileInput = document.getElementById('verify-file');
        if (!fileInput.files[0]) {
            alert('Выберите файл');
            return;
        }
        data = fileInput.files[0];
    }

    // Получаем подпись
    const signatureFileInput = document.getElementById('verify-signature-file');
    if (!signatureFileInput.files[0]) {
        alert('Выберите файл подписи');
        return;
    }

    try {
        showLoading('Проверка подписи...');

        const signatureBase64 = await plugin.loadSignatureFromFile(signatureFileInput.files[0]);
        const result = await plugin.verifySignature(data, signatureBase64);

        hideLoading();

        if (result.success) {
            const validClass = result.is_valid ? 'verify-valid' : 'verify-invalid';
            const validText = result.is_valid ? '✓ Подпись действительна' : '✗ Подпись недействительна';

            document.getElementById('verify-result').style.display = 'block';
            document.getElementById('verify-result-content').innerHTML = `
                <div class="${validClass}">
                    <h4>${validText}</h4>
                </div>
                <p><strong>Подписчик:</strong> ${result.signature_info.signer_name}</p>
                <p><strong>Отпечаток:</strong> <code>${result.signature_info.signer_thumbprint}</code></p>
                <p><strong>Время подписи:</strong> ${plugin.formatDate(result.signature_info.signing_time)}</p>
                <p><strong>Алгоритм:</strong> ${result.signature_info.signature_type}</p>
                ${result.error ? `<p><strong>Ошибка:</strong> ${result.error}</p>` : ''}
            `;
        } else {
            alert(`Ошибка проверки: ${result.error}`);
        }
    } catch (error) {
        hideLoading();
        alert(`Ошибка: ${error.message}`);
        console.error('Verify signature error:', error);
    }
}

// ==================== Статистика ====================

async function loadStatistics() {
    try {
        const result = await plugin.getStatistics();

        if (result.success) {
            const stats = result.statistics;

            document.getElementById('stats-content').innerHTML = `
                <div class="stat-card">
                    <div class="stat-value">${stats.total_certificates}</div>
                    <div class="stat-label">Сертификатов</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">${stats.total_signatures_created}</div>
                    <div class="stat-label">Подписей создано</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">${stats.total_signatures_verified}</div>
                    <div class="stat-label">Подписей проверено</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">${Math.floor(stats.service_uptime / 60)}м</div>
                    <div class="stat-label">Время работы</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">${stats.service_version}</div>
                    <div class="stat-label">Версия сервиса</div>
                </div>
            `;
        } else {
            alert(`Ошибка загрузки статистики: ${result.error}`);
        }
    } catch (error) {
        alert(`Ошибка: ${error.message}`);
        console.error('Load statistics error:', error);
    }
}

// ==================== Вспомогательные функции ====================

function showStatus(elementId, type, message) {
    const element = document.getElementById(elementId);
    element.className = `status status-${type}`;
    element.textContent = message;
}

function showLoading(message) {
    // Простой индикатор загрузки
    const overlay = document.createElement('div');
    overlay.id = 'loading-overlay';
    overlay.innerHTML = `
        <div class="loading-spinner">
            <div class="spinner"></div>
            <p>${message}</p>
        </div>
    `;
    document.body.appendChild(overlay);
}

function hideLoading() {
    const overlay = document.getElementById('loading-overlay');
    if (overlay) {
        overlay.remove();
    }
}

// ==================== Event Listeners ====================

// Переключение типа данных для подписи
document.querySelectorAll('input[name="sign-data-type"]').forEach(radio => {
    radio.addEventListener('change', (e) => {
        if (e.target.value === 'text') {
            document.getElementById('sign-text-input').style.display = 'block';
            document.getElementById('sign-file-input').style.display = 'none';
        } else {
            document.getElementById('sign-text-input').style.display = 'none';
            document.getElementById('sign-file-input').style.display = 'block';
        }
    });
});

// Переключение типа данных для проверки
document.querySelectorAll('input[name="verify-data-type"]').forEach(radio => {
    radio.addEventListener('change', (e) => {
        if (e.target.value === 'text') {
            document.getElementById('verify-text-input').style.display = 'block';
            document.getElementById('verify-file-input').style.display = 'none';
        } else {
            document.getElementById('verify-text-input').style.display = 'none';
            document.getElementById('verify-file-input').style.display = 'block';
        }
    });
});
