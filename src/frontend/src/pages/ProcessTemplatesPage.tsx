import React, { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams, useLocation } from 'react-router-dom';
import { api } from '../api/client';
import DagView from '../components/DagView';
import PageHeader from '../components/PageHeader';
import SectionCard from '../components/SectionCard';
import { ProcessTemplate, Task } from '../types';

function parseTemplateTasks(templateJson: string): Task[] {
  const parsed = JSON.parse(templateJson);
  const tasks = Array.isArray(parsed.tasks) ? parsed.tasks : [];
  return tasks.map((task: Record<string, unknown>, index: number) => ({
    id: index + 1,
    project_id: 0,
    task_code: String(task.task_code || `T${index + 1}`),
    task_name: String(task.task_name || task.task_code || `任务 ${index + 1}`),
    assignee_label: String(task.assignee || ''),
    description: String(task.description || ''),
    assignee_agent_id: null,
    status: 'pending',
    depends_on_json: JSON.stringify(Array.isArray(task.depends_on) ? task.depends_on : []),
    expected_output_path: String(task.expected_output || ''),
    result_file_path: null,
    usage_file_path: null,
    last_error: null,
    timeout_minutes: 10,
    dispatched_at: null,
    completed_at: null,
  }));
}

function getTemplateSummary(templateJson: string): { name: string; description: string } {
  try {
    const parsed = JSON.parse(templateJson);
    return {
      name: String(parsed.plan_name || ''),
      description: String(parsed.description || ''),
    };
  } catch {
    return { name: '', description: '' };
  }
}

export default function ProcessTemplatesPage() {
  const { templateId } = useParams<{ templateId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const isNew = location.pathname.endsWith('/new');
  const isEdit = location.pathname.endsWith('/edit') || isNew;
  const [templates, setTemplates] = useState<ProcessTemplate[]>([]);
  const [template, setTemplate] = useState<ProcessTemplate | null>(null);
  const [descriptionInput, setDescriptionInput] = useState('');
  const [generatedPrompt, setGeneratedPrompt] = useState('');
  const [jsonInput, setJsonInput] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [previewTasks, setPreviewTasks] = useState<Task[]>([]);
  const [selectedPreviewTaskId, setSelectedPreviewTaskId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    async function fetchData() {
      setLoading(true);
      setError('');
      try {
        if (templateId) {
          const item = await api.get<ProcessTemplate>(`/api/process-templates/${templateId}`);
          setTemplate(item);
          setJsonInput(item.template_json);
          setName(item.name);
          setDescription(item.description || '');
          setPreviewTasks(parseTemplateTasks(item.template_json));
        } else if (!isNew) {
          const list = await api.get<ProcessTemplate[]>('/api/process-templates');
          setTemplates(list);
        }
      } catch (err) {
        setError(`加载流程模版失败：${err}`);
      } finally {
        setLoading(false);
      }
    }
    fetchData();
  }, [isNew, templateId]);

  const pageTitle = useMemo(() => {
    if (isNew) return '新建流程模版';
    if (isEdit) return '修改流程模版';
    if (templateId) return '流程模版详情';
    return '流程模版';
  }, [isEdit, isNew, templateId]);

  async function handleGeneratePrompt() {
    setError('');
    try {
      if (!descriptionInput.trim()) {
        throw new Error('请先输入流程描述。');
      }
      const result = await api.post<{ prompt: string }>('/api/process-templates/generate-prompt', {
        scenario: description,
        description: descriptionInput,
      });
      setGeneratedPrompt(result.prompt);
    } catch (err) {
      setError(`生成 Prompt 失败：${err}`);
    }
  }

  function handlePreview() {
    setError('');
    try {
      const tasks = parseTemplateTasks(jsonInput);
      if (!tasks.length) {
        throw new Error('JSON 中没有 tasks。');
      }
      const summary = getTemplateSummary(jsonInput);
      if (!name.trim() && summary.name) {
        setName(summary.name);
      }
      if (!description.trim() && summary.description) {
        setDescription(summary.description);
      }
      setPreviewTasks(tasks);
      setSelectedPreviewTaskId(tasks[0]?.id ?? null);
    } catch (err) {
      setError(`预览失败：${err}`);
    }
  }

  async function handleSave() {
    setSaving(true);
    setError('');
    try {
      const payload = {
        name,
        description,
        template_json: jsonInput,
      };
      const saved = isNew
        ? await api.post<ProcessTemplate>('/api/process-templates', payload)
        : await api.put<ProcessTemplate>(`/api/process-templates/${templateId}`, payload);
      navigate(`/templates/${saved.id}`);
    } catch (err) {
      setError(`保存流程模版失败：${err}`);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(item: ProcessTemplate) {
    if (!confirm(`确认删除流程模版“${item.name}”吗？`)) return;
    setError('');
    try {
      await api.delete(`/api/process-templates/${item.id}`);
      setTemplates((current) => current.filter((candidate) => candidate.id !== item.id));
    } catch (err) {
      setError(`删除流程模版失败：${err}`);
    }
  }

  if (loading) return <div className="page-loading">正在加载流程模版...</div>;

  if (!isNew && !templateId) {
    return (
      <div className="page">
        <PageHeader title="流程模版">
          <Link className="btn btn-primary" to="/templates/new">新建流程模版</Link>
        </PageHeader>
        {error && <div className="error-message">{error}</div>}
        <div className="template-list">
          {templates.map((item) => (
            <section key={item.id} className="template-row">
              <div>
                <h3>{item.name}</h3>
                <p>{item.description || '暂无适用场景说明'}</p>
                <div className="template-row-meta">
                  <span>需要 {item.agent_count} 个 Agent</span>
                  <span>{item.agent_slots.join(' / ') || '无槽位'}</span>
                </div>
              </div>
              <div className="template-row-actions">
                <Link className="btn btn-secondary" to={`/templates/${item.id}`}>查看详情</Link>
                {item.can_edit && <Link className="btn btn-secondary" to={`/templates/${item.id}/edit`}>修改</Link>}
                {item.can_edit && (
                  <button className="btn btn-danger" onClick={() => handleDelete(item)}>删除</button>
                )}
              </div>
            </section>
          ))}
          {!templates.length && (
            <div className="empty-state compact-empty-state">
              <p>还没有流程模版。</p>
              <Link className="btn btn-primary" to="/templates/new">新建流程模版</Link>
            </div>
          )}
        </div>
      </div>
    );
  }

  if (!isEdit && template) {
    return (
      <div className="page">
        <PageHeader title={pageTitle}>
          <button className="btn btn-ghost" onClick={() => navigate('/templates')}>返回列表</button>
          {template.can_edit && <Link className="btn btn-primary" to={`/templates/${template.id}/edit`}>修改</Link>}
        </PageHeader>
        {error && <div className="error-message">{error}</div>}
        <div className="template-detail-layout">
          <SectionCard title={template.name} description={template.description || '暂无适用场景说明'}>
            <div className="template-row-meta">
              <span>需要 {template.agent_count} 个 Agent</span>
              <span>{template.agent_slots.join(' / ')}</span>
            </div>
            <pre className="template-json-preview">{template.template_json}</pre>
          </SectionCard>
          <section className="plan-chart-panel plan-chart-panel-large">
            <DagView
              tasks={previewTasks}
              selectedTaskId={selectedPreviewTaskId}
              onSelectTask={setSelectedPreviewTaskId}
              missingPredecessorIds={new Set()}
            />
          </section>
        </div>
      </div>
    );
  }

  return (
    <div className="page page-narrow">
      <PageHeader title={pageTitle}>
        <button className="btn btn-ghost" onClick={() => navigate(templateId ? `/templates/${templateId}` : '/templates')}>
          返回
        </button>
      </PageHeader>
      {error && <div className="error-message">{error}</div>}

      <SectionCard
        title="1. 基本信息"
        description="模版名称保存时必须有值；适用场景会作为 Prompt 上下文，也会作为模版说明保存"
      >
        <div className="form-group">
          <label htmlFor="template-name">模版名称</label>
          <input
            id="template-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="例如：多人代码审查流程"
          />
          <div className="helper-text">可先留空，保存时会回退取 JSON 中的 plan_name。</div>
        </div>
        <div className="form-group">
          <label htmlFor="template-description">适用场景</label>
          <textarea
            id="template-description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            rows={3}
            placeholder="例如：多人代码审查、前后端并行开发"
          />
          <div className="helper-text">允许为空；为空时保存会回退取 JSON 中的 description。</div>
        </div>
      </SectionCard>

      {isNew && (
        <SectionCard title="2. 输入描述" description="说明流程对应的任务特性、关键 task 和期望 agent 角色">
          <textarea
            value={descriptionInput}
            onChange={(event) => setDescriptionInput(event.target.value)}
            rows={4}
            className="import-textarea"
            placeholder="例如：适用于代码审查，先做初审，再做深度审查，最后汇总结论。"
          />
          <div className="plan-prompt-actions">
            <button className="btn btn-secondary" onClick={handleGeneratePrompt}>生成 Prompt</button>
          </div>
          {generatedPrompt && (
            <textarea
              value={generatedPrompt}
              onChange={(event) => setGeneratedPrompt(event.target.value)}
              rows={10}
              className="import-textarea"
            />
          )}
        </SectionCard>
      )}

      <SectionCard title={isNew ? '3. 粘贴 JSON' : '2. 编辑 JSON'} description="粘贴外部 agent 生成的流程模版 JSON，可手工调整后预览">
        <textarea
          value={jsonInput}
          onChange={(event) => setJsonInput(event.target.value)}
          rows={14}
          className="import-textarea input-mono"
          placeholder='{"plan_name":"代码审查流程","description":"适用于代码审查","tasks":[]}'
        />
        <div className="plan-prompt-actions">
          <button className="btn btn-secondary" onClick={handlePreview}>预览</button>
        </div>
      </SectionCard>

      {previewTasks.length > 0 && (
        <SectionCard title="预览">
          <div className="template-preview-frame">
            <DagView
              tasks={previewTasks}
              selectedTaskId={selectedPreviewTaskId}
              onSelectTask={setSelectedPreviewTaskId}
              missingPredecessorIds={new Set()}
            />
          </div>
        </SectionCard>
      )}

      <div className="form-actions">
        <button className="btn btn-ghost" onClick={() => navigate(templateId ? `/templates/${templateId}` : '/templates')}>取消</button>
        <button className="btn btn-primary" onClick={handleSave} disabled={saving || !jsonInput.trim()}>
          {saving ? '保存中...' : '保存'}
        </button>
      </div>
    </div>
  );
}
