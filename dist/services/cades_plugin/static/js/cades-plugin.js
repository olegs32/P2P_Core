/**
 * CAdES Plugin - JavaScript Library
 *
 * Самописный аналог CAdES plugin для работы с ЭЦП в браузере
 * Работает через HTTP API вместо NPAPI/ActiveX плагинов
 *
 * @version 1.0.0
 * @author P2P Core Team
 */

class CAdESPlugin {
    /**
     * Создать экземпляр CAdES Plugin
     *
     * @param {string} apiUrl - URL сервиса (например, "https://localhost:8001/rpc")
     * @param {string} authToken - JWT токен для аутентификации
     */
    constructor(apiUrl, authToken = null) {
        this.apiUrl = apiUrl;
        this.authToken = authToken;
        this.serviceName = "cades_plugin";
    }

    /**
     * Выполнить RPC вызов к сервису
     *
     * @private
     * @param {string} method - Имя метода
     * @param {object} params - Параметры метода
     * @returns {Promise<object>} - Результат вызова
     */
    async _rpcCall(method, params = {}) {
        const headers = {
            'Content-Type': 'application/json'
        };

        if (this.authToken) {
            headers['Authorization'] = `Bearer ${this.authToken}`;
        }

        const response = await fetch(this.apiUrl, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({
                method: `${this.serviceName}/${method}`,
                params: params,
                id: this._generateRequestId()
            })
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const result = await response.json();

        if (result.error) {
            throw new Error(result.error.message || 'RPC call failed');
        }

        return result.result;
    }

    /**
     * Генерировать уникальный ID запроса
     *
     * @private
     * @returns {string} - Уникальный ID
     */
    _generateRequestId() {
        return `req_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    }

    /**
     * Конвертировать File/Blob в Base64
     *
     * @private
     * @param {File|Blob} file - Файл для конвертации
     * @returns {Promise<string>} - Base64 строка
     */
    async _fileToBase64(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => {
                const base64 = reader.result.split(',')[1];
                resolve(base64);
            };
            reader.onerror = reject;
            reader.readAsDataURL(file);
        });
    }

    /**
     * Конвертировать строку в Base64
     *
     * @private
     * @param {string} str - Строка
     * @returns {string} - Base64 строка
     */
    _stringToBase64(str) {
        return btoa(unescape(encodeURIComponent(str)));
    }

    /**
     * Конвертировать Base64 в Blob
     *
     * @private
     * @param {string} base64 - Base64 строка
     * @param {string} mimeType - MIME тип
     * @returns {Blob} - Blob объект
     */
    _base64ToBlob(base64, mimeType = 'application/octet-stream') {
        const byteCharacters = atob(base64);
        const byteNumbers = new Array(byteCharacters.length);

        for (let i = 0; i < byteCharacters.length; i++) {
            byteNumbers[i] = byteCharacters.charCodeAt(i);
        }

        const byteArray = new Uint8Array(byteNumbers);
        return new Blob([byteArray], { type: mimeType });
    }

    // ==================== Public API ====================

    /**
     * Импортировать сертификат из PKCS#12 файла
     *
     * @param {File|Blob} pfxFile - PKCS#12 файл (.pfx, .p12)
     * @param {string} password - Пароль для расшифровки
     * @returns {Promise<object>} - Информация о импортированном сертификате
     *
     * @example
     * const fileInput = document.getElementById('pfx-file');
     * const pfxFile = fileInput.files[0];
     * const result = await plugin.importCertificate(pfxFile, '12345678');
     * console.log('Imported:', result.certificate.subject_cn);
     */
    async importCertificate(pfxFile, password) {
        const pfxBase64 = await this._fileToBase64(pfxFile);

        return await this._rpcCall('import_certificate', {
            pfx_base64: pfxBase64,
            password: password
        });
    }

    /**
     * Получить список всех сертификатов
     *
     * @returns {Promise<object>} - Список сертификатов
     *
     * @example
     * const result = await plugin.listCertificates();
     * for (const cert of result.certificates) {
     *     console.log(`${cert.subject_cn} - ${cert.thumbprint}`);
     * }
     */
    async listCertificates() {
        return await this._rpcCall('list_certificates');
    }

    /**
     * Получить информацию о сертификате по отпечатку
     *
     * @param {string} thumbprint - SHA1 отпечаток сертификата
     * @returns {Promise<object>} - Информация о сертификате
     *
     * @example
     * const result = await plugin.getCertificate('A1B2C3D4...');
     * console.log('Subject:', result.certificate.subject_name);
     */
    async getCertificate(thumbprint) {
        return await this._rpcCall('get_certificate', {
            thumbprint: thumbprint
        });
    }

    /**
     * Удалить сертификат
     *
     * @param {string} thumbprint - SHA1 отпечаток сертификата
     * @returns {Promise<object>} - Результат операции
     *
     * @example
     * const result = await plugin.deleteCertificate('A1B2C3D4...');
     * console.log(result.message);
     */
    async deleteCertificate(thumbprint) {
        return await this._rpcCall('delete_certificate', {
            thumbprint: thumbprint
        });
    }

    /**
     * Подписать данные (создать электронную подпись)
     *
     * @param {string|Blob|File} data - Данные для подписи
     * @param {string} thumbprint - Отпечаток сертификата
     * @param {string} password - Пароль от закрытого ключа
     * @param {boolean} detached - Создать отсоединенную подпись (по умолчанию true)
     * @returns {Promise<object>} - Подпись в формате CAdES-BES
     *
     * @example
     * // Подписать строку
     * const result = await plugin.signData(
     *     'Hello, World!',
     *     'A1B2C3D4...',
     *     '12345678'
     * );
     * console.log('Signature:', result.signature);
     *
     * // Подписать файл
     * const fileInput = document.getElementById('file');
     * const file = fileInput.files[0];
     * const result = await plugin.signData(file, thumbprint, password);
     */
    async signData(data, thumbprint, password, detached = true) {
        let dataBase64;

        if (typeof data === 'string') {
            dataBase64 = this._stringToBase64(data);
        } else if (data instanceof Blob || data instanceof File) {
            dataBase64 = await this._fileToBase64(data);
        } else {
            throw new Error('Data must be string, Blob, or File');
        }

        return await this._rpcCall('sign_data', {
            data_base64: dataBase64,
            thumbprint: thumbprint,
            password: password,
            detached: detached
        });
    }

    /**
     * Проверить электронную подпись
     *
     * @param {string|Blob|File} data - Исходные данные
     * @param {string} signatureBase64 - Подпись в Base64
     * @returns {Promise<object>} - Результат проверки
     *
     * @example
     * const result = await plugin.verifySignature(
     *     'Hello, World!',
     *     'eyJzaWduYXR1cmUiOi...'
     * );
     * console.log('Valid:', result.is_valid);
     * console.log('Signer:', result.signature_info.signer_name);
     */
    async verifySignature(data, signatureBase64) {
        let dataBase64;

        if (typeof data === 'string') {
            dataBase64 = this._stringToBase64(data);
        } else if (data instanceof Blob || data instanceof File) {
            dataBase64 = await this._fileToBase64(data);
        } else {
            throw new Error('Data must be string, Blob, or File');
        }

        return await this._rpcCall('verify_signature', {
            data_base64: dataBase64,
            signature_base64: signatureBase64
        });
    }

    /**
     * Получить публичный ключ сертификата в формате PEM
     *
     * @param {string} thumbprint - Отпечаток сертификата
     * @param {string} password - Пароль от PKCS#12
     * @returns {Promise<object>} - Сертификат в PEM формате
     *
     * @example
     * const result = await plugin.getPublicKey('A1B2C3D4...', '12345678');
     * console.log(result.certificate_pem);
     */
    async getPublicKey(thumbprint, password) {
        return await this._rpcCall('get_public_key', {
            thumbprint: thumbprint,
            password: password
        });
    }

    /**
     * Получить статистику использования сервиса
     *
     * @returns {Promise<object>} - Статистика
     *
     * @example
     * const result = await plugin.getStatistics();
     * console.log('Total certificates:', result.statistics.total_certificates);
     * console.log('Signatures created:', result.statistics.total_signatures_created);
     */
    async getStatistics() {
        return await this._rpcCall('get_statistics');
    }

    // ==================== Utility Methods ====================

    /**
     * Скачать подпись как файл
     *
     * @param {string} signatureBase64 - Подпись в Base64
     * @param {string} filename - Имя файла для скачивания
     */
    downloadSignature(signatureBase64, filename = 'signature.sig') {
        const blob = this._base64ToBlob(signatureBase64);
        const url = URL.createObjectURL(blob);

        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);

        URL.revokeObjectURL(url);
    }

    /**
     * Загрузить подпись из файла
     *
     * @param {File} signatureFile - Файл с подписью
     * @returns {Promise<string>} - Подпись в Base64
     */
    async loadSignatureFromFile(signatureFile) {
        return await this._fileToBase64(signatureFile);
    }

    /**
     * Форматировать дату
     *
     * @param {string} isoDate - Дата в ISO формате
     * @returns {string} - Отформатированная дата
     */
    formatDate(isoDate) {
        const date = new Date(isoDate);
        return date.toLocaleString();
    }

    /**
     * Проверить валидность сертификата по датам
     *
     * @param {object} certificate - Объект сертификата
     * @returns {boolean} - true если сертификат действителен
     */
    isCertificateValid(certificate) {
        const now = new Date();
        const validFrom = new Date(certificate.valid_from);
        const validTo = new Date(certificate.valid_to);

        return now >= validFrom && now <= validTo;
    }

    /**
     * Получить информацию о сертификате в читаемом виде
     *
     * @param {object} certificate - Объект сертификата
     * @returns {string} - Форматированная информация
     */
    getCertificateInfo(certificate) {
        const isValid = this.isCertificateValid(certificate);
        const status = isValid ? '✓ Действителен' : '✗ Недействителен';

        return `
Субъект: ${certificate.subject_cn}
Издатель: ${certificate.issuer_cn}
Отпечаток: ${certificate.thumbprint}
Действителен с: ${this.formatDate(certificate.valid_from)}
Действителен до: ${this.formatDate(certificate.valid_to)}
Статус: ${status}
Закрытый ключ: ${certificate.has_private_key ? 'Да' : 'Нет'}
        `.trim();
    }
}

// Экспорт для использования в модулях
if (typeof module !== 'undefined' && module.exports) {
    module.exports = CAdESPlugin;
}

// Глобальный объект для использования в браузере
if (typeof window !== 'undefined') {
    window.CAdESPlugin = CAdESPlugin;
}
