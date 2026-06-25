"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  Check,
  Cpu,
  DatabaseZap,
  KeyRound,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Star,
  Trash2,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  type LlmModel,
  type ModelProvider,
  createModel,
  createProvider,
  deleteModel,
  deleteProvider,
  listModels,
  listProviders,
  setDefaultModel,
  updateModel,
  updateProvider,
} from "@/lib/api";

type ProviderForm = {
  id?: string;
  name: string;
  api_key: string;
  base_url: string;
};

type ModelForm = {
  id?: string;
  name: string;
  provider_id: string;
  model_id: string;
  max_tokens: string;
  temperature_default: string;
  is_default: boolean;
};

const emptyProvider: ProviderForm = {
  name: "",
  api_key: "",
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
};

const emptyModel: ModelForm = {
  name: "",
  provider_id: "",
  model_id: "",
  max_tokens: "4096",
  temperature_default: "0.7",
  is_default: false,
};

export default function ModelsPage() {
  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [models, setModels] = useState<LlmModel[]>([]);
  const [providerForm, setProviderForm] = useState<ProviderForm | null>(null);
  const [modelForm, setModelForm] = useState<ModelForm | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const [nextProviders, nextModels] = await Promise.all([
        listProviders(),
        listModels(),
      ]);
      setProviders(nextProviders);
      setModels(nextModels);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  const defaultModel = useMemo(
    () => models.find((model) => model.is_default) ?? null,
    [models]
  );

  function startProviderCreate() {
    setProviderForm(emptyProvider);
  }

  function startProviderEdit(provider: ModelProvider) {
    setProviderForm({
      id: provider.id,
      name: provider.name,
      api_key: "",
      base_url: provider.base_url,
    });
  }

  function startModelCreate() {
    setModelForm({
      ...emptyModel,
      provider_id: providers[0]?.id ?? "",
      is_default: models.length === 0,
    });
  }

  function startModelEdit(model: LlmModel) {
    setModelForm({
      id: model.id,
      name: model.name,
      provider_id: model.provider_id,
      model_id: model.model_id,
      max_tokens: String(model.max_tokens),
      temperature_default: String(model.temperature_default),
      is_default: model.is_default,
    });
  }

  async function saveProvider(e: React.FormEvent) {
    e.preventDefault();
    if (!providerForm) return;
    setSaving(true);
    setError(null);
    try {
      if (providerForm.id) {
        const payload: { name: string; base_url: string; api_key?: string } = {
          name: providerForm.name.trim(),
          base_url: providerForm.base_url.trim(),
        };
        if (providerForm.api_key.trim()) payload.api_key = providerForm.api_key.trim();
        await updateProvider(providerForm.id, payload);
      } else {
        await createProvider({
          name: providerForm.name.trim(),
          api_key: providerForm.api_key.trim(),
          base_url: providerForm.base_url.trim(),
        });
      }
      setProviderForm(null);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function saveModel(e: React.FormEvent) {
    e.preventDefault();
    if (!modelForm) return;
    setSaving(true);
    setError(null);
    const payload = {
      name: modelForm.name.trim(),
      model_id: modelForm.model_id.trim(),
      max_tokens: Number(modelForm.max_tokens),
      temperature_default: Number(modelForm.temperature_default),
      is_default: modelForm.is_default,
    };
    try {
      if (modelForm.id) {
        await updateModel(modelForm.id, payload);
      } else {
        await createModel({
          ...payload,
          provider_id: modelForm.provider_id,
        });
      }
      setModelForm(null);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function onDeleteProvider(provider: ModelProvider) {
    if (!confirm(`Delete provider "${provider.name}"? Models under it will be removed.`)) return;
    setError(null);
    try {
      await deleteProvider(provider.id);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function onDeleteModel(model: LlmModel) {
    if (!confirm(`Delete model "${model.name}"? Bound agents will fall back to default.`)) return;
    setError(null);
    try {
      await deleteModel(model.id);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function onSetDefault(model: LlmModel) {
    setError(null);
    try {
      await setDefaultModel(model.id);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="page-stack">
      <header className="page-header mb-6">
        <h1 className="page-title">模型管理</h1>
        <p className="page-subtitle">
          管理 OpenAI 兼容 Provider、模型默认值和 Agent 可选运行模型
        </p>
      </header>

      <div className="gradient-divider" />

      <div className="action-bar">
        <div className="flex flex-wrap items-center gap-3">
          <div className="header-stat">
            <DatabaseZap className="h-4 w-4" />
            {providers.length} providers
          </div>
          <div className="header-stat">
            <Cpu className="h-4 w-4" />
            {models.length} models
          </div>
          <div className="header-stat header-stat-success">
            <Star className="h-4 w-4" />
            {defaultModel ? defaultModel.name : "No default"}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="icon"
            onClick={refresh}
            disabled={loading}
            className="group active:scale-90 transition-transform duration-150"
            title="Refresh"
          >
            <RefreshCw
              className={`h-4 w-4 transition-transform duration-500 ease-out ${
                loading ? "animate-spin" : "group-hover:rotate-180"
              }`}
            />
          </Button>
          <Button variant="outline" onClick={startProviderCreate}>
            <KeyRound className="h-4 w-4" />
            Provider
          </Button>
          <Button onClick={startModelCreate} disabled={providers.length === 0}>
            <Plus className="h-4 w-4" />
            Model
          </Button>
        </div>
      </div>

      {error && (
        <div className="alert alert-error mb-4">
          <AlertCircle className="h-4 w-4 shrink-0" />
          {error}
        </div>
      )}

      {providerForm && (
        <section className="detail-panel animate-fade-in-up border-[var(--color-brand-muted)]">
          <div className="detail-panel-header">
            <div>
              <h2 className="detail-panel-title">
                <KeyRound className="h-[18px] w-[18px]" />
                {providerForm.id ? "Edit provider" : "New provider"}
              </h2>
              <p className="detail-panel-desc">
                API key is encrypted before storage and never returned by the API
              </p>
            </div>
            <Button variant="ghost" size="icon" onClick={() => setProviderForm(null)}>
              <X className="h-4 w-4" />
            </Button>
          </div>
          <form onSubmit={saveProvider} className="grid gap-4 lg:grid-cols-[1fr_1fr_1.2fr_auto] lg:items-end">
            <div className="space-y-1.5">
              <Label htmlFor="provider-name">Name</Label>
              <Input
                id="provider-name"
                value={providerForm.name}
                onChange={(e) => setProviderForm({ ...providerForm, name: e.target.value })}
                placeholder="DashScope"
                required
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="provider-key">API Key</Label>
              <Input
                id="provider-key"
                type="password"
                value={providerForm.api_key}
                onChange={(e) => setProviderForm({ ...providerForm, api_key: e.target.value })}
                placeholder={providerForm.id ? "Leave blank to keep current" : "sk-..."}
                required={!providerForm.id}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="provider-base">Base URL</Label>
              <Input
                id="provider-base"
                value={providerForm.base_url}
                onChange={(e) => setProviderForm({ ...providerForm, base_url: e.target.value })}
                placeholder="https://api.openai.com/v1"
                required
              />
            </div>
            <Button type="submit" disabled={saving}>
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
              Save
            </Button>
          </form>
        </section>
      )}

      {modelForm && (
        <section className="detail-panel animate-fade-in-up border-[var(--color-brand-muted)]">
          <div className="detail-panel-header">
            <div>
              <h2 className="detail-panel-title">
                <Cpu className="h-[18px] w-[18px]" />
                {modelForm.id ? "Edit model" : "New model"}
              </h2>
              <p className="detail-panel-desc">
                Model ID is the provider-facing identifier passed to agent runtimes
              </p>
            </div>
            <Button variant="ghost" size="icon" onClick={() => setModelForm(null)}>
              <X className="h-4 w-4" />
            </Button>
          </div>
          <form onSubmit={saveModel} className="space-y-4">
            <div className="grid gap-4 lg:grid-cols-3">
              <div className="space-y-1.5">
                <Label htmlFor="model-name">Name</Label>
                <Input
                  id="model-name"
                  value={modelForm.name}
                  onChange={(e) => setModelForm({ ...modelForm, name: e.target.value })}
                  placeholder="Qwen Plus"
                  required
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="model-provider">Provider</Label>
                <select
                  id="model-provider"
                  value={modelForm.provider_id}
                  onChange={(e) => setModelForm({ ...modelForm, provider_id: e.target.value })}
                  className="select-control"
                  disabled={Boolean(modelForm.id)}
                  required
                >
                  {providers.map((provider) => (
                    <option key={provider.id} value={provider.id}>
                      {provider.name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="model-id">Model ID</Label>
                <Input
                  id="model-id"
                  value={modelForm.model_id}
                  onChange={(e) => setModelForm({ ...modelForm, model_id: e.target.value })}
                  placeholder="qwen-plus"
                  required
                />
              </div>
            </div>
            <div className="grid gap-4 lg:grid-cols-[1fr_1fr_auto] lg:items-end">
              <div className="space-y-1.5">
                <Label htmlFor="max-tokens">Max tokens</Label>
                <Input
                  id="max-tokens"
                  type="number"
                  min={1}
                  value={modelForm.max_tokens}
                  onChange={(e) => setModelForm({ ...modelForm, max_tokens: e.target.value })}
                  required
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="temperature">Temperature</Label>
                <Input
                  id="temperature"
                  type="number"
                  min={0}
                  max={2}
                  step={0.1}
                  value={modelForm.temperature_default}
                  onChange={(e) =>
                    setModelForm({ ...modelForm, temperature_default: e.target.value })
                  }
                  required
                />
              </div>
              <label className="toggle-label h-9">
                <input
                  type="checkbox"
                  checked={modelForm.is_default}
                  onChange={(e) => setModelForm({ ...modelForm, is_default: e.target.checked })}
                />
                Set as default
              </label>
            </div>
            <div className="flex gap-2">
              <Button type="submit" disabled={saving}>
                {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                Save model
              </Button>
              <Button type="button" variant="outline" onClick={() => setModelForm(null)}>
                Cancel
              </Button>
            </div>
          </form>
        </section>
      )}

      <div className="grid gap-4 xl:grid-cols-[minmax(280px,360px)_1fr]">
        <section className="detail-panel">
          <div className="detail-panel-header">
            <div>
              <h2 className="detail-panel-title">
                <DatabaseZap className="h-[18px] w-[18px]" />
                Providers
              </h2>
              <p className="detail-panel-desc">Base URLs for OpenAI-compatible APIs</p>
            </div>
          </div>

          {loading ? (
            <div className="space-y-2">
              {[1, 2].map((i) => (
                <div key={i} className="skeleton h-20 rounded-[var(--radius-md)]" />
              ))}
            </div>
          ) : providers.length === 0 ? (
            <div className="empty-state !py-10">
              <KeyRound className="empty-state-icon h-9 w-9" />
              <h3 className="empty-state-title">No providers</h3>
              <p className="empty-state-desc">Create one before adding models</p>
            </div>
          ) : (
            <div className="space-y-2">
              {providers.map((provider) => (
                <div key={provider.id} className="model-provider-item">
                  <div className="min-w-0">
                    <div className="font-semibold text-[var(--color-text-primary)]">
                      {provider.name}
                    </div>
                    <div className="truncate font-mono text-xs text-[var(--color-text-tertiary)]">
                      {provider.base_url}
                    </div>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <Button variant="ghost" size="icon" onClick={() => startProviderEdit(provider)}>
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button variant="ghost" size="icon" onClick={() => onDeleteProvider(provider)}>
                      <Trash2 className="h-4 w-4 text-[var(--color-error-icon)]" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="detail-panel">
          <div className="detail-panel-header">
            <div>
              <h2 className="detail-panel-title">
                <Cpu className="h-[18px] w-[18px]" />
                Models
              </h2>
              <p className="detail-panel-desc">Default model is used when an Agent has no binding</p>
            </div>
          </div>

          {loading ? (
            <div className="space-y-2">
              {[1, 2, 3].map((i) => (
                <div key={i} className="skeleton h-20 rounded-[var(--radius-md)]" />
              ))}
            </div>
          ) : models.length === 0 ? (
            <div className="empty-state !py-12">
              <Cpu className="empty-state-icon h-10 w-10" />
              <h3 className="empty-state-title">No models</h3>
              <p className="empty-state-desc">Add a provider-backed model to enable bindings</p>
            </div>
          ) : (
            <div className="models-table">
              {models.map((model) => (
                <div key={model.id} className={`models-row ${model.is_default ? "default" : ""}`}>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-semibold text-[var(--color-text-primary)]">
                        {model.name}
                      </span>
                      {model.is_default && (
                        <span className="status-badge running !py-1">
                          <Star className="h-3.5 w-3.5" />
                          Default
                        </span>
                      )}
                    </div>
                    <div className="mt-1 flex flex-wrap gap-2 text-xs text-[var(--color-text-secondary)]">
                      <span className="code-pill">{model.provider_name ?? "Provider"}</span>
                      <span className="code-pill">{model.model_id}</span>
                      <span>{model.max_tokens} tokens</span>
                      <span>temp {model.temperature_default}</span>
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    {!model.is_default && (
                      <Button variant="outline" size="sm" onClick={() => onSetDefault(model)}>
                        <Star className="h-4 w-4" />
                        Default
                      </Button>
                    )}
                    <Button variant="ghost" size="icon" onClick={() => startModelEdit(model)}>
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button variant="ghost" size="icon" onClick={() => onDeleteModel(model)}>
                      <Trash2 className="h-4 w-4 text-[var(--color-error-icon)]" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
