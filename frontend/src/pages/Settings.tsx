import { useEffect, useState } from 'react'
import toast from 'react-hot-toast'
import { modelApi } from '@/api/client'
import { useModelStore } from '@/stores/modelStore'
import { useLocaleStore } from '@/stores/localeStore'
import ModelConfig from '@/components/ModelConfig/ModelConfig'
import NetworkConfig from '@/components/NetworkConfig'
import ZoteroIntegration from '@/components/ZoteroIntegration'
import OpenSourceInfo from '@/components/OpenSourceInfo'
import { safeErrorMessage } from '@/utils/safeError'

export default function Settings() {
  const { t } = useLocaleStore()
  const {
    config,
    testResult,
    isTesting,
    isSaving,
    setConfig,
    setFullConfig,
    setTestResult,
    setIsTesting,
    setIsSaving,
  } = useModelStore()
  const [embeddingTestResult, setEmbeddingTestResult] = useState<import('@/api/types').ModelTestResponse | null>(null)
  const [isEmbeddingTesting, setIsEmbeddingTesting] = useState(false)
  const [fallbackTestResult, setFallbackTestResult] = useState<import('@/api/types').ModelTestResponse | null>(null)
  const [isFallbackTesting, setIsFallbackTesting] = useState(false)

  useEffect(() => {
    let active = true
    void modelApi.getConfig()
      .then((response) => {
        if (active) setFullConfig(response.data)
      })
      .catch(() => undefined)
    return () => {
      active = false
    }
  }, [setFullConfig])

  const handleTest = async () => {
    setIsTesting(true)
    setTestResult(null)

    try {
      const response = await modelApi.testConnection({
        provider: config.provider,
        model_name: config.model_name,
        api_key: config.api_key,
        base_url: config.base_url,
      })
      setTestResult(response.data)
    } catch (err: any) {
      setTestResult({
        success: false,
        latency_ms: null,
        model_info: null,
        error: safeErrorMessage(err, t('settings.connectionFailed')),
      })
    } finally {
      setIsTesting(false)
    }
  }

  const handleSave = async () => {
    setIsSaving(true)

    try {
      await modelApi.saveConfig(config)
      toast.success(t('settings.connectionSuccess'))
    } catch (err: any) {
      toast.error(safeErrorMessage(err, t('settings.connectionFailed')))
    } finally {
      setIsSaving(false)
    }
  }

  const handleEmbeddingTest = async () => {
    if (!config.embedding) return
    setIsEmbeddingTesting(true)
    setEmbeddingTestResult(null)
    try {
      const response = await modelApi.testEmbedding(config.embedding)
      setEmbeddingTestResult(response.data)
    } catch (err: any) {
      setEmbeddingTestResult({
        success: false,
        latency_ms: null,
        model_info: null,
        error: safeErrorMessage(err, t('settings.connectionFailed')),
      })
    } finally {
      setIsEmbeddingTesting(false)
    }
  }

  const handleFallbackTest = async () => {
    if (!config.fallback?.provider || !config.fallback.model_name) return
    setIsFallbackTesting(true)
    setFallbackTestResult(null)
    try {
      const response = await modelApi.testConnection({
        provider: config.fallback.provider,
        model_name: config.fallback.model_name,
        api_key: config.fallback.api_key,
        base_url: config.fallback.base_url,
      })
      setFallbackTestResult(response.data)
    } catch (err: any) {
      setFallbackTestResult({
        success: false,
        latency_ms: null,
        model_info: null,
        error: safeErrorMessage(err, t('settings.connectionFailed')),
      })
    } finally {
      setIsFallbackTesting(false)
    }
  }

  return (
    <div className="min-h-[calc(100vh-3.5rem)] bg-gray-50 dark:bg-gray-950 px-4 py-8">
      <div className="max-w-2xl mx-auto">
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">{t('settings.title')}</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            {t('settings.modelConfig')}
          </p>
        </div>

        <ModelConfig
          config={config}
          testResult={testResult}
          isTesting={isTesting}
          isSaving={isSaving}
          onConfigChange={setConfig}
          onTest={handleTest}
          onSave={handleSave}
          fallbackTestResult={fallbackTestResult}
          isFallbackTesting={isFallbackTesting}
          onFallbackTest={handleFallbackTest}
          embeddingTestResult={embeddingTestResult}
          isEmbeddingTesting={isEmbeddingTesting}
          onEmbeddingTest={handleEmbeddingTest}
        />

        <div className="mt-6">
          <ZoteroIntegration />
        </div>

        <div className="mt-6">
          <NetworkConfig />
        </div>
        <OpenSourceInfo />
      </div>
    </div>
  )
}
