/* ── Vue 3 SPA — 搬砖记账 ──────────────────────────────────── */

const { createApp, ref, reactive, computed, onMounted, watch } = Vue;

// ── Auth ───────────────────────────────────────────────────────

const TOKEN_KEY = 'brick_token';
const USER_KEY = 'brick_user';

const authToken = ref(localStorage.getItem(TOKEN_KEY) || '');
const authUser = ref(JSON.parse(localStorage.getItem(USER_KEY) || 'null'));

function isLoggedIn() { return !!authToken.value; }

function saveAuth(token, user) {
    authToken.value = token;
    authUser.value = user;
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
}

function logout() {
    authToken.value = '';
    authUser.value = null;
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    currentPage.value = 'login';
}

// ── API Helper (with auth header) ──────────────────────────────

async function api(url, opts = {}) {
    const headers = opts.headers || {};
    if (authToken.value) {
        headers['Authorization'] = 'Bearer ' + authToken.value;
    }
    const r = await fetch(url, { ...opts, headers });
    const d = await r.json();
    if (!d.ok) {
        if (r.status === 401) { logout(); }
        throw new Error(d.error || '请求失败');
    }
    return d.data;
}

const API = {
    get: (url) => api(url),
    post: (url, body) => api(url, { method: 'POST', body }),
    put: (url, data) => api(url, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }),
    del: (url) => api(url, { method: 'DELETE' }),
};

// ── Toast ──────────────────────────────────────────────────────

const toasts = ref([]);
let toastId = 0;
function addToast(message, type = 'info', duration = 3000) {
    const id = ++toastId;
    toasts.value.push({ id, message, type });
    setTimeout(() => { toasts.value = toasts.value.filter(t => t.id !== id); }, duration);
}

// ── Hash Router ────────────────────────────────────────────────

const currentPage = ref('orders');
function onHashChange() {
    const hash = location.hash.replace('#/', '');
    currentPage.value = hash || (isLoggedIn() ? 'orders' : 'login');
}
window.addEventListener('hashchange', onHashChange);

// ── Format Helpers ─────────────────────────────────────────────

function fmtDate(d) { return d || '-'; }
function fmtMoney(v) { return v != null && v !== '' ? `¥${Number(v).toFixed(2)}` : '-'; }
function fmtProfit(v) {
    if (v == null || v === '') return '-';
    const n = Number(v);
    return n >= 0 ? `+¥${n.toFixed(2)}` : `-¥${Math.abs(n).toFixed(2)}`;
}

// ── Login Page ─────────────────────────────────────────────────

const LoginPage = {
    template: '#login-template',
    setup() {
        const form = reactive({ username: '', password: '' });
        const isRegister = ref(false);
        const loading = ref(false);
        const error = ref('');

        async function submit() {
            error.value = '';
            if (!form.username || !form.password) {
                error.value = '请填写用户名和密码';
                return;
            }
            loading.value = true;
            try {
                const endpoint = isRegister.value ? '/api/auth/register' : '/api/auth/login';
                const result = await api(endpoint, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username: form.username, password: form.password }),
                });
                saveAuth(result.token, result.user);
                addToast(isRegister.value ? '注册成功' : '登录成功', 'success');
                location.hash = '#/orders';
                onHashChange();
            } catch (e) {
                error.value = e.message;
            } finally {
                loading.value = false;
            }
        }

        return { form, isRegister, loading, error, submit, logout };
    },
};

// ── Order List Page ─────────────────────────────────────────────

const OrderListPage = {
    template: '#orders-template',
    setup() {
        const orders = ref([]);
        const total = ref(0);
        const page = ref(1);
        const limit = ref(20);
        const totalPages = ref(1);
        const statusFilter = ref('');
        const platformFilter = ref('');
        const keyword = ref('');
        const loading = ref(false);
        const stats = ref(null);
        const editingOrder = ref(null);
        const showEditModal = ref(false);
        const showDeleteConfirm = ref(null);
        const showImagePreview = ref(null);
        const editForm = reactive({ model: '', size: '', platform: '', expense: null, order_date: '', received: null, sell_date: '', fee: 0, status: 'selling', note: '' });
        const editProfit = computed(() => {
            const e = parseFloat(editForm.expense);
            const r = parseFloat(editForm.received);
            const f = parseFloat(editForm.fee) || 0;
            if (!isNaN(e) && !isNaN(r)) return r - e - f;
            return null;
        });

        async function load() {
            loading.value = true;
            try {
                const params = new URLSearchParams({ page: page.value, limit: limit.value });
                if (statusFilter.value) params.set('status', statusFilter.value);
                if (platformFilter.value) params.set('platform', platformFilter.value);
                if (keyword.value) params.set('keyword', keyword.value);
                const [data, s] = await Promise.all([
                    API.get('/api/orders?' + params.toString()),
                    API.get('/api/stats/overview'),
                ]);
                orders.value = data.orders;
                total.value = data.total;
                totalPages.value = data.total_pages;
                stats.value = s;
            } catch (e) {
                addToast('加载失败: ' + e.message, 'error');
            } finally {
                loading.value = false;
            }
        }

        function goPage(p) { page.value = p; load(); }

        function openEdit(order) {
            editingOrder.value = order;
            editForm.model = order.model || '';
            editForm.size = order.size || '';
            editForm.platform = order.platform || '';
            editForm.expense = order.expense;
            editForm.order_date = order.order_date || '';
            editForm.received = order.received;
            editForm.sell_date = order.sell_date || '';
            editForm.fee = order.fee || 0;
            editForm.status = order.status || 'selling';
            editForm.note = order.note || '';
            showEditModal.value = true;
        }

        async function saveEdit() {
            try {
                const payload = { ...editForm };
                if (payload.expense === '' || payload.expense === null) payload.expense = null;
                if (payload.received === '' || payload.received === null) payload.received = null;
                await API.put(`/api/orders/${editingOrder.value.id}`, payload);
                addToast('订单已更新', 'success');
                showEditModal.value = false;
                load();
            } catch (e) {
                addToast('更新失败: ' + e.message, 'error');
            }
        }

        function confirmDelete(order) { showDeleteConfirm.value = order; }
        async function doDelete() {
            try {
                await API.del(`/api/orders/${showDeleteConfirm.value.id}`);
                addToast('订单已删除', 'success');
                showDeleteConfirm.value = null;
                load();
            } catch (e) {
                addToast('删除失败: ' + e.message, 'error');
            }
        }

        function previewImage(img) { showImagePreview.value = img; }
        function onSearch() { page.value = 1; load(); }

        onMounted(load);
        watch(statusFilter, () => { page.value = 1; load(); });
        watch(platformFilter, () => { page.value = 1; load(); });

        function fmtStatus(s) { return s === 'sold' ? '已售' : '在售'; }
        function platformLabel(p) { return p || '-'; }

        return {
            orders, total, page, limit, totalPages, loading, stats,
            statusFilter, platformFilter, keyword,
            showEditModal, editForm, editProfit, editingOrder,
            showDeleteConfirm, showImagePreview,
            openEdit, saveEdit, confirmDelete, doDelete,
            goPage, onSearch, previewImage,
            fmtMoney, fmtProfit, fmtDate, fmtStatus, platformLabel,
        };
    },
};

// ── Add Order Page ──────────────────────────────────────────────

const AddOrderPage = {
    template: '#addorder-template',
    setup() {
        const form = reactive({ model: '', size: '', platform: '', expense: null, order_date: '', note: '' });
        const imageFile = ref(null);
        const imagePreview = ref('');
        const uploading = ref(false);
        const ocrLoading = ref(false);
        const ocrTexts = ref([]);
        const ocrFields = ref({});
        const ocrError = ref('');
        const ocrDone = ref(false);
        const submitting = ref(false);
        const dragOver = ref(false);

        function onFileSelect(e) { const file = e.target.files?.[0]; if (file) handleFile(file); }
        function onDrop(e) {
            dragOver.value = false;
            const file = e.dataTransfer?.files?.[0];
            if (file) handleFile(file);
        }

        function handleFile(file) {
            if (!file.type.startsWith('image/')) { addToast('请上传图片', 'error'); return; }
            imageFile.value = file;
            const reader = new FileReader();
            reader.onload = e => { imagePreview.value = e.target.result; };
            reader.readAsDataURL(file);
            ocrDone.value = false; ocrError.value = ''; ocrTexts.value = []; ocrFields.value = {};
            runOCR(file);
        }

        async function runOCR(file) {
            ocrLoading.value = true; ocrError.value = '';
            try {
                const fd = new FormData();
                fd.append('image', file);
                const result = await API.post('/api/ocr', fd);
                const fields = result.fields;
                ocrTexts.value = result.raw_texts || [];
                ocrFields.value = fields || {};
                ocrDone.value = true;
                if (fields.model) form.model = fields.model;
                if (fields.size) form.size = fields.size;
                if (fields.platform) form.platform = fields.platform;
                if (fields.expense) form.expense = fields.expense;
                if (fields.order_date) form.order_date = fields.order_date;
                addToast('OCR 识别完成', 'success');
            } catch (e) {
                ocrError.value = e.message;
                addToast('OCR 失败: ' + e.message, 'error');
            } finally { ocrLoading.value = false; }
        }

        async function submit() {
            if (!imageFile.value) { addToast('请上传订单截图', 'error'); return; }
            submitting.value = true;
            try {
                const fd = new FormData();
                fd.append('image', imageFile.value);
                fd.append('model', form.model);
                fd.append('size', form.size);
                fd.append('platform', form.platform);
                fd.append('expense', form.expense || '');
                fd.append('order_date', form.order_date);
                fd.append('note', form.note);
                await API.post('/api/orders', fd);
                addToast('订单已保存', 'success');
                form.model = ''; form.size = ''; form.platform = '';
                form.expense = null; form.order_date = ''; form.note = '';
                imageFile.value = null; imagePreview.value = '';
                ocrTexts.value = []; ocrFields.value = {}; ocrError.value = ''; ocrDone.value = false;
                location.hash = '#/orders';
            } catch (e) { addToast('保存失败: ' + e.message, 'error');
            } finally { submitting.value = false; }
        }

        function resetForm() {
            form.model = ''; form.size = ''; form.platform = '';
            form.expense = null; form.order_date = ''; form.note = '';
            imageFile.value = null; imagePreview.value = '';
            ocrTexts.value = []; ocrFields.value = {}; ocrError.value = ''; ocrDone.value = false;
        }

        return { form, imageFile, imagePreview, uploading, ocrLoading, ocrTexts, ocrFields, ocrError, ocrDone, submitting, dragOver, onFileSelect, onDrop, handleFile, submit, resetForm };
    },
};

// ── App ─────────────────────────────────────────────────────────

const app = createApp({
    setup() {
        // On first load, check auth and redirect
        if (!isLoggedIn()) {
            setTimeout(() => { if (currentPage.value !== 'login') { currentPage.value = 'login'; } }, 0);
        }
        return { currentPage, toasts, authUser, isLoggedIn, logout };
    },
    components: { LoginPage, OrderListPage, AddOrderPage },
});

app.component('toast-container', {
    setup() { return { toasts }; },
    template: '<div class="toast-container"><div v-for="t in toasts" :key="t.id" :class="[\'toast\', \'toast-\' + t.type]"><i :class="t.type === \'success\' ? \'fas fa-check-circle\' : t.type === \'error\' ? \'fas fa-exclamation-circle\' : \'fas fa-info-circle\'"></i> {{ t.message }}</div></div>',
});

app.mount('#app');
