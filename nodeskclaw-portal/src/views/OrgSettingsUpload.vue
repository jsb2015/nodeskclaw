<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { AlertTriangle, CheckCircle2, Info, Loader2, Save } from 'lucide-vue-next'
import { useToast } from '@/composables/useToast'
import { resolveApiErrorMessage } from '@/i18n/error'
import api from '@/services/api'
import CustomSelect, { type SelectOption } from '@/components/shared/CustomSelect.vue'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'

const { t } = useI18n()
const toast = useToast()

const loading = ref(false)
const saving = ref(false)
const policy = ref<UploadPolicy | null>(null)

const form = ref<UploadForm>({
  upload_chat_attachment_max_mb: '20',
  upload_chat_attachment_max_count: '5',
  upload_chat_attachment_retention_days: '90',
  upload_shared_file_max_mb: '200',
  upload_large_file_max_mb: '2048',
  upload_chunked_upload_threshold_mb: '50',
  upload_chunk_size_mb: '8',
  upload_workspace_quota_mb: '10240',
  upload_gateway_proxy_body_size_mb: '50',
  upload_proxy_read_timeout_seconds: '300',
  upload_proxy_send_timeout_seconds: '300',
  upload_blocked_extensions: '.exe,.bat,.cmd,.sh',
  upload_allowed_content_types: '',
  upload_security_scan_mode: 'metadata_only',
  upload_scanner_provider: 'none',
  upload_scanner_endpoint: '',
  upload_scanner_timeout_seconds: '60',
  upload_scanner_max_retries: '3',
  upload_scanner_max_file_mb: '2048',
  upload_scanner_fail_closed: true,
})
const savedForm = ref<UploadForm>({ ...form.value })
const validationError = ref<string | null>(null)

interface SurfacePolicy {
  max_file_size_bytes: number
  max_files_per_message?: number
  retention_days?: number
  chunked_upload_threshold_bytes?: number
  chunk_size_bytes?: number
  max_workspace_total_bytes?: number
}

interface UploadPolicy {
  backend: string
  storage_status: string
  storage_reason_code?: string | null
  direct_upload_supported: boolean
  surfaces: {
    chat_attachment: SurfacePolicy
    shared_file: SurfacePolicy
    large_input: SurfacePolicy
  }
  gateway: {
    proxy_body_size_bytes: number
    is_gateway_lower_than_policy: boolean
  }
  security: {
    scan_mode: string
    scanner_configured: boolean
    scanner_provider: string
    scanner_fail_closed: boolean
  }
}

type UploadForm = {
  upload_chat_attachment_max_mb: string
  upload_chat_attachment_max_count: string
  upload_chat_attachment_retention_days: string
  upload_shared_file_max_mb: string
  upload_large_file_max_mb: string
  upload_chunked_upload_threshold_mb: string
  upload_chunk_size_mb: string
  upload_workspace_quota_mb: string
  upload_gateway_proxy_body_size_mb: string
  upload_proxy_read_timeout_seconds: string
  upload_proxy_send_timeout_seconds: string
  upload_blocked_extensions: string
  upload_allowed_content_types: string
  upload_security_scan_mode: string
  upload_scanner_provider: string
  upload_scanner_endpoint: string
  upload_scanner_timeout_seconds: string
  upload_scanner_max_retries: string
  upload_scanner_max_file_mb: string
  upload_scanner_fail_closed: boolean
}

type UploadFormKey = keyof UploadForm

const scanModeOptions = computed<SelectOption[]>(() => [
  { value: 'metadata_only', label: t('orgSettings.uploadScanModeMetadata') },
  { value: 'async_required', label: t('orgSettings.uploadScanModeAsync') },
  { value: 'disabled', label: t('orgSettings.uploadScanModeDisabled') },
])

const scannerProviderOptions = computed<SelectOption[]>(() => [
  { value: 'none', label: t('orgSettings.uploadScannerNone') },
  { value: 'http', label: t('orgSettings.uploadScannerHttp') },
  { value: 'clamav', label: t('orgSettings.uploadScannerClamav') },
])

const positiveIntegerKeys: UploadFormKey[] = [
  'upload_chat_attachment_max_mb',
  'upload_chat_attachment_max_count',
  'upload_chat_attachment_retention_days',
  'upload_shared_file_max_mb',
  'upload_large_file_max_mb',
  'upload_chunked_upload_threshold_mb',
  'upload_chunk_size_mb',
  'upload_workspace_quota_mb',
  'upload_gateway_proxy_body_size_mb',
  'upload_proxy_read_timeout_seconds',
  'upload_proxy_send_timeout_seconds',
  'upload_scanner_timeout_seconds',
  'upload_scanner_max_file_mb',
]

const editableKeys: UploadFormKey[] = [
  'upload_chat_attachment_max_mb',
  'upload_chat_attachment_max_count',
  'upload_chat_attachment_retention_days',
  'upload_shared_file_max_mb',
  'upload_large_file_max_mb',
  'upload_chunked_upload_threshold_mb',
  'upload_chunk_size_mb',
  'upload_workspace_quota_mb',
  'upload_gateway_proxy_body_size_mb',
  'upload_proxy_read_timeout_seconds',
  'upload_proxy_send_timeout_seconds',
  'upload_blocked_extensions',
  'upload_allowed_content_types',
  'upload_security_scan_mode',
  'upload_scanner_provider',
  'upload_scanner_endpoint',
  'upload_scanner_timeout_seconds',
  'upload_scanner_max_retries',
  'upload_scanner_max_file_mb',
  'upload_scanner_fail_closed',
]

const effectivePolicyRows = computed(() => {
  if (!policy.value) return []
  return [
    {
      label: t('orgSettings.uploadEffectiveChat'),
      value: formatMb(policy.value.surfaces.chat_attachment.max_file_size_bytes),
    },
    {
      label: t('orgSettings.uploadEffectiveShared'),
      value: formatMb(policy.value.surfaces.shared_file.max_file_size_bytes),
    },
    {
      label: t('orgSettings.uploadEffectiveLarge'),
      value: formatMb(policy.value.surfaces.large_input.max_file_size_bytes),
    },
    {
      label: t('orgSettings.uploadEffectiveGateway'),
      value: formatMb(policy.value.gateway.proxy_body_size_bytes),
    },
  ]
})

const warningItems = computed(() => {
  const items: string[] = []
  if (policy.value?.storage_status !== 'available') {
    items.push(t('orgSettings.uploadStorageUnavailableWarning', {
      reason: policy.value?.storage_reason_code || t('orgSettings.uploadUnknownReason'),
    }))
  }
  if (policy.value && (!policy.value.direct_upload_supported || policy.value.backend === 'local')) {
    items.push(t('orgSettings.uploadDirectUploadWarning', { backend: policy.value.backend }))
  }
  if (isGatewayLowerThanPolicy()) {
    items.push(t('orgSettings.uploadGatewayWarning'))
  }
  if (form.value.upload_security_scan_mode === 'async_required' && !isScannerConfigured()) {
    items.push(t('orgSettings.uploadScannerUnavailableWarning'))
  }
  return items
})

function toNumber(key: UploadFormKey): number {
  const value = form.value[key]
  return typeof value === 'boolean' ? 0 : Number(value)
}

function isPositiveInteger(value: number): boolean {
  return Number.isInteger(value) && value > 0
}

function isNonNegativeInteger(value: number): boolean {
  return Number.isInteger(value) && value >= 0
}

function isScannerConfigured(): boolean {
  return form.value.upload_scanner_provider !== 'none' && !!form.value.upload_scanner_endpoint.trim()
}

function isGatewayLowerThanPolicy(): boolean {
  const gateway = Number(form.value.upload_gateway_proxy_body_size_mb) || 0
  const appMax = Math.max(
    Number(form.value.upload_chat_attachment_max_mb) || 0,
    Number(form.value.upload_shared_file_max_mb) || 0,
    Number(form.value.upload_large_file_max_mb) || 0,
    Number(form.value.upload_chunked_upload_threshold_mb) || 0,
  )
  return gateway > 0 && appMax > gateway
}

function formatMb(bytes?: number): string {
  if (!bytes) return t('common.dash')
  return t('orgSettings.uploadMbValue', { value: Math.round(bytes / 1024 / 1024) })
}

function normalizeValue(key: UploadFormKey): string {
  const value = form.value[key]
  return typeof value === 'boolean' ? String(value) : value.trim()
}

function validateForm(): boolean {
  for (const key of positiveIntegerKeys) {
    if (!isPositiveInteger(toNumber(key))) {
      validationError.value = t('orgSettings.uploadPositiveIntegerError')
      return false
    }
  }
  if (!isNonNegativeInteger(Number(form.value.upload_scanner_max_retries))) {
    validationError.value = t('orgSettings.uploadNonNegativeIntegerError')
    return false
  }
  if (toNumber('upload_chat_attachment_max_mb') > toNumber('upload_shared_file_max_mb')) {
    validationError.value = t('orgSettings.uploadChatSharedLimitError')
    return false
  }
  if (form.value.upload_security_scan_mode === 'async_required' && !isScannerConfigured()) {
    validationError.value = t('orgSettings.uploadScannerRequiredError')
    return false
  }
  validationError.value = null
  return true
}

function orderedKeysToSave(): UploadFormKey[] {
  const keys = new Set(editableKeys.filter(key => {
    const current = savedForm.value[key]
    const next = form.value[key]
    return String(current).trim() !== String(next).trim()
  }))
  const ordered: UploadFormKey[] = []
  const add = (key: UploadFormKey) => {
    if (keys.delete(key)) ordered.push(key)
  }
  const targetShared = Number(form.value.upload_shared_file_max_mb)
  const currentChat = Number(savedForm.value.upload_chat_attachment_max_mb)
  if (targetShared < currentChat) {
    add('upload_chat_attachment_max_mb')
    add('upload_shared_file_max_mb')
  } else {
    add('upload_shared_file_max_mb')
    add('upload_chat_attachment_max_mb')
  }
  if (form.value.upload_security_scan_mode !== 'async_required') {
    add('upload_security_scan_mode')
  }
  for (const key of editableKeys) {
    if (key === 'upload_chat_attachment_max_mb'
      || key === 'upload_shared_file_max_mb'
      || key === 'upload_security_scan_mode') continue
    add(key)
  }
  if (form.value.upload_security_scan_mode === 'async_required') {
    add('upload_security_scan_mode')
  }
  for (const key of keys) add(key)
  return ordered
}

async function loadSettings() {
  loading.value = true
  try {
    const [settingsRes, policyRes] = await Promise.all([
      api.get('/settings'),
      api.get('/upload/policy'),
    ])
    const data = settingsRes.data.data as Record<string, string | null>
    for (const key of Object.keys(form.value) as UploadFormKey[]) {
      if (data[key] == null) continue
      if (key === 'upload_scanner_fail_closed') {
        form.value[key] = data[key] !== 'false'
      } else {
        form.value[key] = data[key] || ''
      }
    }
    policy.value = policyRes.data.data as UploadPolicy
    savedForm.value = { ...form.value }
  } catch (e: unknown) {
    toast.error(resolveApiErrorMessage(e, t('orgSettings.uploadLoadFailed')))
  } finally {
    loading.value = false
  }
}

async function refreshPolicy() {
  const res = await api.get('/upload/policy')
  policy.value = res.data.data as UploadPolicy
}

async function handleSave() {
  if (!validateForm()) return
  saving.value = true
  try {
    for (const key of orderedKeysToSave()) {
      await api.put(`/settings/${key}`, { value: normalizeValue(key) })
    }
    await refreshPolicy()
    savedForm.value = { ...form.value }
    toast.success(t('orgSettings.uploadSaved'))
  } catch (e: unknown) {
    toast.error(resolveApiErrorMessage(e, t('orgSettings.uploadSaveFailed')))
  } finally {
    saving.value = false
  }
}

onMounted(loadSettings)
</script>

<template>
  <div class="space-y-5">
    <div>
      <h2 class="text-lg font-semibold">{{ t('orgSettings.uploadTitle') }}</h2>
      <p class="text-sm text-muted-foreground mt-1">{{ t('orgSettings.uploadDescription') }}</p>
    </div>

    <div v-if="loading" class="flex items-center justify-center py-12">
      <Loader2 class="w-6 h-6 animate-spin text-muted-foreground" />
    </div>

    <template v-else>
      <div class="rounded-md border border-border bg-muted/30 p-3">
        <div class="flex items-center gap-2 text-sm font-medium">
          <Info class="w-4 h-4 text-primary" />
          <span>{{ t('orgSettings.uploadEffectivePolicy') }}</span>
        </div>
        <div class="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
          <div v-for="row in effectivePolicyRows" :key="row.label" class="min-w-0">
            <div class="text-[11px] text-muted-foreground">{{ row.label }}</div>
            <div class="mt-1 truncate text-sm font-mono">{{ row.value }}</div>
          </div>
        </div>
        <div v-if="policy" class="mt-3 flex flex-wrap gap-2 text-xs">
          <span class="rounded border border-border px-2 py-1 font-mono">{{ policy.backend }}</span>
          <span class="rounded border border-border px-2 py-1 font-mono">{{ policy.storage_status }}</span>
          <span class="rounded border border-border px-2 py-1">
            {{ policy.direct_upload_supported ? t('orgSettings.uploadDirectEnabled') : t('orgSettings.uploadDirectDisabled') }}
          </span>
        </div>
      </div>

      <div v-if="warningItems.length" class="space-y-2">
        <div
          v-for="item in warningItems"
          :key="item"
          class="flex items-start gap-2 rounded-md border border-yellow-500/30 bg-yellow-500/10 p-3"
        >
          <AlertTriangle class="w-4 h-4 shrink-0 text-yellow-600 mt-0.5" />
          <p class="text-sm text-yellow-700 dark:text-yellow-400">{{ item }}</p>
        </div>
      </div>

      <div v-if="validationError" class="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-3">
        <AlertTriangle class="w-4 h-4 shrink-0 text-destructive mt-0.5" />
        <p class="text-sm text-destructive">{{ validationError }}</p>
      </div>

      <div class="space-y-3 border border-border rounded-lg p-4">
        <h3 class="text-sm font-semibold">{{ t('orgSettings.uploadChatAttachment') }}</h3>
        <div class="grid gap-4 md:grid-cols-3">
          <div class="space-y-1.5">
            <label class="text-xs text-muted-foreground">{{ t('orgSettings.uploadMaxFileSizeMb') }}</label>
            <Input v-model="form.upload_chat_attachment_max_mb" type="number" min="1" class="h-8 text-sm font-mono" />
          </div>
          <div class="space-y-1.5">
            <label class="text-xs text-muted-foreground">{{ t('orgSettings.uploadMaxCount') }}</label>
            <Input v-model="form.upload_chat_attachment_max_count" type="number" min="1" class="h-8 text-sm font-mono" />
          </div>
          <div class="space-y-1.5">
            <label class="text-xs text-muted-foreground">{{ t('orgSettings.uploadRetentionDays') }}</label>
            <Input v-model="form.upload_chat_attachment_retention_days" type="number" min="1" class="h-8 text-sm font-mono" />
          </div>
        </div>
      </div>

      <div class="space-y-3 border border-border rounded-lg p-4">
        <h3 class="text-sm font-semibold">{{ t('orgSettings.uploadSharedFile') }}</h3>
        <div class="grid gap-4 md:grid-cols-2">
          <div class="space-y-1.5">
            <label class="text-xs text-muted-foreground">{{ t('orgSettings.uploadMaxFileSizeMb') }}</label>
            <Input v-model="form.upload_shared_file_max_mb" type="number" min="1" class="h-8 text-sm font-mono" />
          </div>
          <div class="space-y-1.5">
            <label class="text-xs text-muted-foreground">{{ t('orgSettings.uploadQuotaTotalMb') }}</label>
            <Input v-model="form.upload_workspace_quota_mb" type="number" min="1" class="h-8 text-sm font-mono" />
          </div>
        </div>
      </div>

      <div class="space-y-3 border border-border rounded-lg p-4">
        <h3 class="text-sm font-semibold">{{ t('orgSettings.uploadLargeFile') }}</h3>
        <div class="grid gap-4 md:grid-cols-3">
          <div class="space-y-1.5">
            <label class="text-xs text-muted-foreground">{{ t('orgSettings.uploadMaxFileSizeMb') }}</label>
            <Input v-model="form.upload_large_file_max_mb" type="number" min="1" class="h-8 text-sm font-mono" />
          </div>
          <div class="space-y-1.5">
            <label class="text-xs text-muted-foreground">{{ t('orgSettings.uploadChunkedThresholdMb') }}</label>
            <Input v-model="form.upload_chunked_upload_threshold_mb" type="number" min="1" class="h-8 text-sm font-mono" />
          </div>
          <div class="space-y-1.5">
            <label class="text-xs text-muted-foreground">{{ t('orgSettings.uploadChunkSizeMb') }}</label>
            <Input v-model="form.upload_chunk_size_mb" type="number" min="1" class="h-8 text-sm font-mono" />
          </div>
        </div>
      </div>

      <div class="space-y-3 border border-border rounded-lg p-4">
        <h3 class="text-sm font-semibold">{{ t('orgSettings.uploadSecurity') }}</h3>
        <div class="grid gap-4 md:grid-cols-2">
          <div class="space-y-1.5">
            <label class="text-xs text-muted-foreground">{{ t('orgSettings.uploadScanMode') }}</label>
            <CustomSelect v-model="form.upload_security_scan_mode" :options="scanModeOptions" trigger-class="w-full justify-between" />
          </div>
          <div class="space-y-1.5">
            <label class="text-xs text-muted-foreground">{{ t('orgSettings.uploadScannerProvider') }}</label>
            <CustomSelect v-model="form.upload_scanner_provider" :options="scannerProviderOptions" trigger-class="w-full justify-between" />
          </div>
          <div class="space-y-1.5 md:col-span-2">
            <label class="text-xs text-muted-foreground">{{ t('orgSettings.uploadScannerEndpoint') }}</label>
            <Input v-model="form.upload_scanner_endpoint" type="text" :placeholder="t('orgSettings.uploadScannerEndpointPlaceholder')" class="h-8 text-sm font-mono" />
          </div>
          <div class="space-y-1.5">
            <label class="text-xs text-muted-foreground">{{ t('orgSettings.uploadScannerTimeoutSeconds') }}</label>
            <Input v-model="form.upload_scanner_timeout_seconds" type="number" min="1" class="h-8 text-sm font-mono" />
          </div>
          <div class="space-y-1.5">
            <label class="text-xs text-muted-foreground">{{ t('orgSettings.uploadScannerMaxRetries') }}</label>
            <Input v-model="form.upload_scanner_max_retries" type="number" min="0" class="h-8 text-sm font-mono" />
          </div>
          <div class="space-y-1.5">
            <label class="text-xs text-muted-foreground">{{ t('orgSettings.uploadScannerMaxFileMb') }}</label>
            <Input v-model="form.upload_scanner_max_file_mb" type="number" min="1" class="h-8 text-sm font-mono" />
          </div>
          <label class="flex items-center justify-between gap-3 rounded-md border border-border p-3 text-sm">
            <span>
              <span class="block font-medium">{{ t('orgSettings.uploadScannerFailClosed') }}</span>
              <span class="block text-xs text-muted-foreground">{{ t('orgSettings.uploadScannerFailClosedHint') }}</span>
            </span>
            <Switch v-model:checked="form.upload_scanner_fail_closed" />
          </label>
          <div class="space-y-1.5 md:col-span-2">
            <label class="text-xs text-muted-foreground">{{ t('orgSettings.uploadBlockedExtensions') }}</label>
            <Input v-model="form.upload_blocked_extensions" type="text" :placeholder="t('orgSettings.uploadBlockedExtensionsPlaceholder')" class="h-8 text-sm font-mono" />
          </div>
          <div class="space-y-1.5 md:col-span-2">
            <label class="text-xs text-muted-foreground">{{ t('orgSettings.uploadAllowedTypes') }}</label>
            <Input v-model="form.upload_allowed_content_types" type="text" :placeholder="t('orgSettings.uploadAllowedTypesPlaceholder')" class="h-8 text-sm font-mono" />
          </div>
        </div>
      </div>

      <div class="space-y-3 border border-border rounded-lg p-4">
        <h3 class="text-sm font-semibold">{{ t('orgSettings.uploadGateway') }}</h3>
        <p class="text-xs text-muted-foreground">{{ t('orgSettings.uploadGatewayHint') }}</p>
        <div class="grid gap-4 md:grid-cols-3">
          <div class="space-y-1.5">
            <label class="text-xs text-muted-foreground">{{ t('orgSettings.uploadGatewayProxyBodyMb') }}</label>
            <Input v-model="form.upload_gateway_proxy_body_size_mb" type="number" min="1" class="h-8 text-sm font-mono" />
          </div>
          <div class="space-y-1.5">
            <label class="text-xs text-muted-foreground">{{ t('orgSettings.uploadGatewayReadTimeoutSeconds') }}</label>
            <Input v-model="form.upload_proxy_read_timeout_seconds" type="number" min="1" class="h-8 text-sm font-mono" />
          </div>
          <div class="space-y-1.5">
            <label class="text-xs text-muted-foreground">{{ t('orgSettings.uploadGatewaySendTimeoutSeconds') }}</label>
            <Input v-model="form.upload_proxy_send_timeout_seconds" type="number" min="1" class="h-8 text-sm font-mono" />
          </div>
        </div>
      </div>

      <div class="flex items-center gap-3 pt-2">
        <Button
          variant="unstyled"
          size="unstyled"
          :disabled="saving"
          class="h-9 px-4 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 flex items-center gap-2"
          @click="handleSave"
        >
          <Loader2 v-if="saving" class="w-4 h-4 animate-spin" />
          <Save v-else class="w-4 h-4" />
          {{ t('orgSettings.uploadSave') }}
        </Button>
        <div v-if="!validationError" class="flex items-center gap-1.5 text-xs text-muted-foreground">
          <CheckCircle2 class="w-3.5 h-3.5" />
          <span>{{ t('orgSettings.uploadValidationReady') }}</span>
        </div>
      </div>
    </template>
  </div>
</template>
