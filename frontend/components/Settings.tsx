import React, { useEffect, useMemo, useState } from 'react';
import {
  createNotificationChannel,
  deleteNotificationChannel,
  getAccountDetails,
  getNotificationChannels,
  getQRLoginPushSettings,
  getSystemSettings,
  testQRLoginPush,
  updateNotificationChannel,
  updateQRLoginPushSettings,
  updateSystemSettings
} from '../services/api';
import { AccountDetail, NotificationChannel, QRLoginPushSettings, SystemSettings } from '../types';
import {
  Bell,
  Check,
  Database,
  Eye,
  EyeOff,
  Mail,
  Plus,
  QrCode,
  RefreshCw,
  Save,
  Send,
  Settings as SettingsIcon,
  Sparkles,
  Trash2
} from 'lucide-react';

const defaultQRSettings: QRLoginPushSettings = {
  enabled: false,
  schedule_time: '09:00',
  timezone: 'Asia/Shanghai',
  account_ids: [],
  channel_ids: [],
  public_base_url: '',
  retry_enabled: true,
  retry_interval_minutes: 30,
  max_attempts: 5,
};

const defaultChannelForm = {
  id: '',
  name: '',
  type: 'serverchan',
  enabled: true,
  sendkey: '',
  token: '',
  topic: '',
  channel: '',
  template: 'html',
  option: '',
  to: '',
};

const supportedPushTypes = new Set(['serverchan', 'pushplus']);

const Settings: React.FC = () => {
  const [settings, setSettings] = useState<SystemSettings | null>(null);
  const [accounts, setAccounts] = useState<AccountDetail[]>([]);
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [qrSettings, setQrSettings] = useState<QRLoginPushSettings>(defaultQRSettings);
  const [channelForm, setChannelForm] = useState(defaultChannelForm);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [qrSaving, setQrSaving] = useState(false);
  const [qrTesting, setQrTesting] = useState(false);
  const [channelSaving, setChannelSaving] = useState(false);
  const [showApiKey, setShowApiKey] = useState(false);
  const [showSmtpPassword, setShowSmtpPassword] = useState(false);

  const pushChannels = useMemo(
    () => channels.filter(channel => supportedPushTypes.has(channel.type)),
    [channels]
  );

  useEffect(() => {
    loadAll();
  }, []);

  const loadAll = async () => {
    setLoading(true);
    try {
      const [systemData, accountData, channelData, qrData] = await Promise.all([
        getSystemSettings(),
        getAccountDetails(),
        getNotificationChannels(),
        getQRLoginPushSettings().catch(() => defaultQRSettings),
      ]);
      setSettings(systemData);
      setAccounts(accountData);
      setChannels(channelData.data || []);
      setQrSettings({ ...defaultQRSettings, ...qrData });
    } catch (e) {
      alert('加载配置失败：' + (e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    if (!settings) return;
    setSaving(true);
    try {
      await updateSystemSettings(settings);
      alert('系统配置已保存');
    } catch (e) {
      alert('保存失败：' + (e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const saveQRSettings = async () => {
    setQrSaving(true);
    try {
      const result = await updateQRLoginPushSettings(qrSettings);
      if (result.data) setQrSettings(result.data);
      alert('扫码推送配置已保存');
    } catch (e) {
      alert('保存扫码推送失败：' + (e as Error).message);
    } finally {
      setQrSaving(false);
    }
  };

  const testQRPush = async () => {
    setQrTesting(true);
    try {
      await testQRLoginPush({
        account_ids: qrSettings.account_ids,
        channel_ids: qrSettings.channel_ids,
      });
      alert('测试二维码已发送，请查看微信推送');
    } catch (e) {
      alert('测试推送失败：' + (e as Error).message);
    } finally {
      setQrTesting(false);
    }
  };

  const toggleAccount = (accountId: string) => {
    const exists = qrSettings.account_ids.includes(accountId);
    setQrSettings({
      ...qrSettings,
      account_ids: exists
        ? qrSettings.account_ids.filter(id => id !== accountId)
        : [...qrSettings.account_ids, accountId],
    });
  };

  const toggleChannel = (channelId: string) => {
    const numericId = Number(channelId);
    const exists = qrSettings.channel_ids.includes(numericId);
    setQrSettings({
      ...qrSettings,
      channel_ids: exists
        ? qrSettings.channel_ids.filter(id => id !== numericId)
        : [...qrSettings.channel_ids, numericId],
    });
  };

  const editChannel = (channel: NotificationChannel) => {
    const config = channel.config || {};
    setChannelForm({
      ...defaultChannelForm,
      id: channel.id,
      name: channel.name,
      type: channel.type === 'pushplus' ? 'pushplus' : 'serverchan',
      enabled: channel.enabled,
      sendkey: config.sendkey || '',
      token: config.token || '',
      topic: config.topic || '',
      channel: config.channel || '',
      template: config.template || 'html',
      option: config.option || '',
      to: config.to || '',
    });
  };

  const saveChannel = async () => {
    if (!channelForm.name.trim()) {
      alert('请填写渠道名称');
      return;
    }

    const config = channelForm.type === 'serverchan'
      ? { sendkey: channelForm.sendkey.trim() }
      : {
          token: channelForm.token.trim(),
          topic: channelForm.topic.trim(),
          channel: channelForm.channel.trim(),
          template: channelForm.template.trim() || 'html',
          option: channelForm.option.trim(),
          to: channelForm.to.trim(),
        };

    setChannelSaving(true);
    try {
      if (channelForm.id) {
        await updateNotificationChannel(channelForm.id, {
          name: channelForm.name.trim(),
          type: channelForm.type,
          enabled: channelForm.enabled,
          config,
        });
      } else {
        await createNotificationChannel({
          name: channelForm.name.trim(),
          type: channelForm.type,
          config,
        });
      }
      setChannelForm(defaultChannelForm);
      const nextChannels = await getNotificationChannels();
      setChannels(nextChannels.data || []);
    } catch (e) {
      alert('保存通知渠道失败：' + (e as Error).message);
    } finally {
      setChannelSaving(false);
    }
  };

  const removeChannel = async (channelId: string) => {
    if (!confirm('确认删除这个通知渠道吗？')) return;
    await deleteNotificationChannel(channelId);
    setChannels((prev) => prev.filter(channel => channel.id !== channelId));
    setQrSettings({
      ...qrSettings,
      channel_ids: qrSettings.channel_ids.filter(id => id !== Number(channelId)),
    });
  };

  if (loading || !settings) {
    return <div className="p-8 text-center text-gray-400">加载配置中...</div>;
  }

  return (
    <div className="max-w-6xl mx-auto space-y-8 animate-fade-in pb-24">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <div className="w-12 h-12 bg-gray-100 rounded-2xl flex items-center justify-center">
            <SettingsIcon className="w-6 h-6 text-gray-600" />
          </div>
          <div>
            <h2 className="text-3xl font-extrabold text-gray-900">系统设置</h2>
            <p className="text-gray-500 mt-1 text-sm font-medium">配置全局自动化规则与通知能力</p>
          </div>
        </div>
        <button
          onClick={loadAll}
          className="px-4 py-2 bg-gray-100 hover:bg-gray-200 rounded-xl font-bold text-gray-700 flex items-center gap-2 transition-colors"
        >
          <RefreshCw className="w-4 h-4" />
          刷新
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        <div className="space-y-8">
          <section className="space-y-4">
            <h3 className="text-lg font-extrabold text-gray-800 flex items-center gap-2">
              <div className="p-1.5 rounded-lg bg-gray-100 text-gray-600">
                <Database className="w-4 h-4" />
              </div>
              基础设置
            </h3>

            <div className="ios-card rounded-[2rem] p-6 bg-white space-y-4">
              {[
                ['允许用户注册', '开启后允许新用户注册账号', 'registration_enabled'],
                ['显示默认登录信息', '登录页面显示默认账号密码提示', 'show_default_login_info'],
                ['登录滑动验证码', '开启后账号密码登录需要完成滑动验证', 'login_captcha_enabled'],
                ['启用商品自动同步', '定时自动获取商品信息到本地数据库', 'item_sync_enabled'],
              ].map(([title, desc, key]) => (
                <div key={key} className="flex items-center justify-between p-4 bg-gray-50 rounded-xl">
                  <div>
                    <div className="font-bold text-gray-900">{title}</div>
                    <div className="text-xs text-gray-500 mt-1">{desc}</div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setSettings({ ...settings, [key]: !settings[key] })}
                    className={`w-14 h-8 rounded-full transition-all relative ${
                      settings[key] ? 'bg-[#FFE815]' : 'bg-gray-300'
                    }`}
                    title={title}
                  >
                    <div
                      className={`w-6 h-6 bg-white rounded-full absolute top-1 transition-all shadow-md ${
                        settings[key] ? 'left-7' : 'left-1'
                      }`}
                    />
                  </button>
                </div>
              ))}

              <div className="space-y-3 px-4">
                <label className="block text-sm font-bold text-gray-800">商品同步间隔（分钟）</label>
                <input
                  type="number"
                  value={Math.round((settings.item_sync_interval || 600) / 60)}
                  onChange={(e) => setSettings({ ...settings, item_sync_interval: (parseInt(e.target.value) || 10) * 60 })}
                  className="w-full ios-input px-4 py-3 rounded-xl"
                  min="1"
                  max="1440"
                />
              </div>

              <div className="space-y-3 px-4">
                <label className="block text-sm font-bold text-gray-800">每次最多同步页数</label>
                <input
                  type="number"
                  value={settings.item_sync_max_pages || 5}
                  onChange={(e) => setSettings({ ...settings, item_sync_max_pages: parseInt(e.target.value) || 5 })}
                  className="w-full ios-input px-4 py-3 rounded-xl"
                  min="1"
                  max="50"
                />
              </div>
            </div>
          </section>

          <section className="space-y-4">
            <h3 className="text-lg font-extrabold text-gray-800 flex items-center gap-2">
              <div className="p-1.5 rounded-lg bg-[#FFE815] text-black">
                <Sparkles className="w-4 h-4" />
              </div>
              AI 智能回复配置
            </h3>

            <div className="ios-card rounded-[2rem] p-6 bg-white space-y-6">
              <div className="space-y-3">
                <label className="block text-sm font-bold text-gray-800">API 地址</label>
                <input
                  type="text"
                  value={settings.ai_api_url || 'https://dashscope.aliyuncs.com/compatible-mode/v1'}
                  onChange={e => setSettings({ ...settings, ai_api_url: e.target.value })}
                  className="w-full ios-input px-4 py-3 rounded-xl text-sm"
                  placeholder="https://api.openai.com/v1"
                />
              </div>

              <div className="space-y-3">
                <label className="block text-sm font-bold text-gray-800">API Key</label>
                <div className="relative">
                  <input
                    type={showApiKey ? 'text' : 'password'}
                    value={settings.ai_api_key || ''}
                    onChange={e => setSettings({ ...settings, ai_api_key: e.target.value })}
                    className="w-full ios-input px-4 py-3 pr-12 rounded-xl font-mono text-sm"
                    placeholder="sk-..."
                  />
                  <button
                    type="button"
                    onClick={() => setShowApiKey(!showApiKey)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 p-2 text-gray-400 hover:text-gray-600 transition-colors"
                    title={showApiKey ? '隐藏' : '显示'}
                  >
                    {showApiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
              </div>

              <div className="space-y-3">
                <label className="block text-sm font-bold text-gray-800">模型</label>
                <input
                  type="text"
                  list="ai-model-options"
                  value={settings.ai_model || 'qwen-plus'}
                  onChange={e => setSettings({ ...settings, ai_model: e.target.value })}
                  className="w-full ios-input px-4 py-3 rounded-xl"
                />
                <datalist id="ai-model-options">
                  <option value="gpt-5.4" />
                  <option value="gpt-5" />
                  <option value="gpt-4.1" />
                  <option value="gpt-4o" />
                  <option value="qwen-plus" />
                  <option value="qwen-turbo" />
                </datalist>
              </div>

              <div className="space-y-3">
                <label className="block text-sm font-bold text-gray-800">默认自动回复内容</label>
                <textarea
                  className="w-full ios-input px-4 py-3 rounded-xl min-h-[100px] text-sm resize-none"
                  value={settings.default_reply || ''}
                  onChange={e => setSettings({ ...settings, default_reply: e.target.value })}
                  placeholder="设置默认的自动回复内容..."
                />
              </div>
            </div>
          </section>
        </div>

        <div className="space-y-8">
          <section className="space-y-4">
            <h3 className="text-lg font-extrabold text-gray-800 flex items-center gap-2">
              <div className="p-1.5 rounded-lg bg-blue-100 text-blue-600">
                <Mail className="w-4 h-4" />
              </div>
              SMTP 邮件配置
            </h3>

            <div className="ios-card rounded-[2rem] p-6 bg-white space-y-6">
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-3">
                  <label className="block text-sm font-bold text-gray-800">SMTP服务器</label>
                  <input
                    type="text"
                    value={settings.smtp_server || ''}
                    onChange={e => setSettings({ ...settings, smtp_server: e.target.value })}
                    placeholder="smtp.qq.com"
                    className="w-full ios-input px-4 py-3 rounded-xl text-sm"
                  />
                </div>
                <div className="space-y-3">
                  <label className="block text-sm font-bold text-gray-800">SMTP端口</label>
                  <input
                    type="number"
                    value={settings.smtp_port || 587}
                    onChange={e => setSettings({ ...settings, smtp_port: parseInt(e.target.value) })}
                    placeholder="587"
                    className="w-full ios-input px-4 py-3 rounded-xl text-sm"
                  />
                </div>
              </div>

              <div className="space-y-3">
                <label className="block text-sm font-bold text-gray-800">发件邮箱</label>
                <input
                  type="email"
                  value={settings.smtp_user || ''}
                  onChange={e => setSettings({ ...settings, smtp_user: e.target.value })}
                  placeholder="your-email@qq.com"
                  className="w-full ios-input px-4 py-3 rounded-xl text-sm"
                />
              </div>

              <div className="space-y-3">
                <label className="block text-sm font-bold text-gray-800">邮箱密码/授权码</label>
                <div className="relative">
                  <input
                    type={showSmtpPassword ? 'text' : 'password'}
                    value={settings.smtp_password || ''}
                    onChange={e => setSettings({ ...settings, smtp_password: e.target.value })}
                    placeholder="输入密码或授权码"
                    className="w-full ios-input px-4 py-3 pr-12 rounded-xl text-sm"
                  />
                  <button
                    type="button"
                    onClick={() => setShowSmtpPassword(!showSmtpPassword)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 p-2 text-gray-400 hover:text-gray-600 transition-colors"
                    title={showSmtpPassword ? '隐藏' : '显示'}
                  >
                    {showSmtpPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
              </div>

              <div className="space-y-3">
                <label className="block text-sm font-bold text-gray-800">发件人显示名</label>
                <input
                  type="text"
                  value={settings.smtp_from || ''}
                  onChange={e => setSettings({ ...settings, smtp_from: e.target.value })}
                  placeholder="闲鱼自动回复系统"
                  className="w-full ios-input px-4 py-3 rounded-xl text-sm"
                />
              </div>
            </div>
          </section>

          <section className="space-y-4">
            <h3 className="text-lg font-extrabold text-gray-800 flex items-center gap-2">
              <div className="p-1.5 rounded-lg bg-emerald-100 text-emerald-700">
                <Bell className="w-4 h-4" />
              </div>
              微信通知渠道
            </h3>

            <div className="ios-card rounded-[2rem] p-6 bg-white space-y-5">
              <div className="grid grid-cols-2 gap-4">
                <input
                  type="text"
                  value={channelForm.name}
                  onChange={e => setChannelForm({ ...channelForm, name: e.target.value })}
                  className="ios-input px-4 py-3 rounded-xl text-sm"
                  placeholder="渠道名称"
                />
                <select
                  value={channelForm.type}
                  onChange={e => setChannelForm({ ...channelForm, type: e.target.value })}
                  className="ios-input px-4 py-3 rounded-xl text-sm"
                >
                  <option value="serverchan">Server酱</option>
                  <option value="pushplus">PushPlus</option>
                </select>
              </div>

              {channelForm.type === 'serverchan' ? (
                <input
                  type="password"
                  value={channelForm.sendkey}
                  onChange={e => setChannelForm({ ...channelForm, sendkey: e.target.value })}
                  className="w-full ios-input px-4 py-3 rounded-xl text-sm"
                  placeholder="Server酱 SENDKEY"
                />
              ) : (
                <div className="space-y-3">
                  <input
                    type="password"
                    value={channelForm.token}
                    onChange={e => setChannelForm({ ...channelForm, token: e.target.value })}
                    className="w-full ios-input px-4 py-3 rounded-xl text-sm"
                    placeholder="PushPlus token"
                  />
                  <div className="grid grid-cols-2 gap-3">
                    <input
                      value={channelForm.topic}
                      onChange={e => setChannelForm({ ...channelForm, topic: e.target.value })}
                      className="ios-input px-4 py-3 rounded-xl text-sm"
                      placeholder="topic"
                    />
                    <input
                      value={channelForm.channel}
                      onChange={e => setChannelForm({ ...channelForm, channel: e.target.value })}
                      className="ios-input px-4 py-3 rounded-xl text-sm"
                      placeholder="channel"
                    />
                    <input
                      value={channelForm.template}
                      onChange={e => setChannelForm({ ...channelForm, template: e.target.value })}
                      className="ios-input px-4 py-3 rounded-xl text-sm"
                      placeholder="template"
                    />
                    <input
                      value={channelForm.to}
                      onChange={e => setChannelForm({ ...channelForm, to: e.target.value })}
                      className="ios-input px-4 py-3 rounded-xl text-sm"
                      placeholder="to"
                    />
                  </div>
                  <input
                    value={channelForm.option}
                    onChange={e => setChannelForm({ ...channelForm, option: e.target.value })}
                    className="w-full ios-input px-4 py-3 rounded-xl text-sm"
                    placeholder="option"
                  />
                </div>
              )}

              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => setChannelForm({ ...channelForm, enabled: !channelForm.enabled })}
                  className={`w-10 h-10 rounded-xl flex items-center justify-center ${channelForm.enabled ? 'bg-[#FFE815]' : 'bg-gray-100'}`}
                  title={channelForm.enabled ? '已启用' : '已停用'}
                >
                  <Check className="w-4 h-4" />
                </button>
                <button
                  type="button"
                  onClick={saveChannel}
                  disabled={channelSaving}
                  className="ios-btn-primary px-5 py-3 rounded-xl font-bold flex items-center gap-2 disabled:opacity-60"
                >
                  <Plus className="w-4 h-4" />
                  {channelForm.id ? '更新渠道' : '添加渠道'}
                </button>
                {channelForm.id && (
                  <button
                    type="button"
                    onClick={() => setChannelForm(defaultChannelForm)}
                    className="px-5 py-3 bg-gray-100 rounded-xl font-bold text-gray-700"
                  >
                    取消编辑
                  </button>
                )}
              </div>

              <div className="space-y-2">
                {pushChannels.map(channel => (
                  <div key={channel.id} className="flex items-center justify-between p-3 bg-gray-50 rounded-xl">
                    <button
                      type="button"
                      onClick={() => editChannel(channel)}
                      className="text-left min-w-0 flex-1"
                    >
                      <div className="font-bold text-gray-900 truncate">{channel.name}</div>
                      <div className="text-xs text-gray-500">{channel.type} · {channel.enabled ? '启用' : '停用'}</div>
                    </button>
                    <button
                      type="button"
                      onClick={() => removeChannel(channel.id)}
                      className="w-9 h-9 rounded-lg bg-white text-red-500 flex items-center justify-center hover:bg-red-50"
                      title="删除"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                ))}
                {pushChannels.length === 0 && (
                  <div className="text-sm text-gray-400 p-4 bg-gray-50 rounded-xl">暂无 Server酱 或 PushPlus 渠道</div>
                )}
              </div>
            </div>
          </section>

          <section className="space-y-4">
            <h3 className="text-lg font-extrabold text-gray-800 flex items-center gap-2">
              <div className="p-1.5 rounded-lg bg-violet-100 text-violet-700">
                <QrCode className="w-4 h-4" />
              </div>
              扫码推送
            </h3>

            <div className="ios-card rounded-[2rem] p-6 bg-white space-y-5">
              <div className="flex items-center justify-between p-4 bg-gray-50 rounded-xl">
                <div>
                  <div className="font-bold text-gray-900">每日推送二维码</div>
                  <div className="text-xs text-gray-500 mt-1">到点后给所选账号生成5分钟扫码登录二维码</div>
                </div>
                <button
                  type="button"
                  onClick={() => setQrSettings({ ...qrSettings, enabled: !qrSettings.enabled })}
                  className={`w-14 h-8 rounded-full transition-all relative ${
                    qrSettings.enabled ? 'bg-[#FFE815]' : 'bg-gray-300'
                  }`}
                  title={qrSettings.enabled ? '已启用' : '已停用'}
                >
                  <div className={`w-6 h-6 bg-white rounded-full absolute top-1 transition-all shadow-md ${qrSettings.enabled ? 'left-7' : 'left-1'}`} />
                </button>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-3">
                  <label className="block text-sm font-bold text-gray-800">推送时间</label>
                  <input
                    type="time"
                    value={qrSettings.schedule_time}
                    onChange={e => setQrSettings({ ...qrSettings, schedule_time: e.target.value })}
                    className="w-full ios-input px-4 py-3 rounded-xl text-sm"
                  />
                </div>
                <div className="space-y-3">
                  <label className="block text-sm font-bold text-gray-800">时区</label>
                  <input
                    value={qrSettings.timezone}
                    onChange={e => setQrSettings({ ...qrSettings, timezone: e.target.value })}
                    className="w-full ios-input px-4 py-3 rounded-xl text-sm"
                    placeholder="Asia/Shanghai"
                  />
                </div>
              </div>

              <div className="space-y-3">
                <label className="block text-sm font-bold text-gray-800">访问基地址</label>
                <input
                  value={qrSettings.public_base_url}
                  onChange={e => setQrSettings({ ...qrSettings, public_base_url: e.target.value })}
                  className="w-full ios-input px-4 py-3 rounded-xl text-sm"
                  placeholder="http://你的电脑局域网IP:8080"
                />
              </div>

              <div className="space-y-3">
                <div className="flex items-center justify-between p-4 bg-gray-50 rounded-xl">
                  <div>
                    <div className="font-bold text-gray-900">过期自动重试</div>
                    <div className="text-xs text-gray-500 mt-1">二维码5分钟未扫码后自动重新推送</div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setQrSettings({ ...qrSettings, retry_enabled: !qrSettings.retry_enabled })}
                    className={`w-14 h-8 rounded-full transition-all relative ${
                      qrSettings.retry_enabled ? 'bg-[#FFE815]' : 'bg-gray-300'
                    }`}
                    title={qrSettings.retry_enabled ? '已启用' : '已停用'}
                  >
                    <div className={`w-6 h-6 bg-white rounded-full absolute top-1 transition-all shadow-md ${qrSettings.retry_enabled ? 'left-7' : 'left-1'}`} />
                  </button>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-3">
                    <label className="block text-sm font-bold text-gray-800">重试间隔（分钟）</label>
                    <input
                      type="number"
                      min={5}
                      max={1440}
                      value={qrSettings.retry_interval_minutes}
                      onChange={e => setQrSettings({ ...qrSettings, retry_interval_minutes: Number(e.target.value) })}
                      className="w-full ios-input px-4 py-3 rounded-xl text-sm"
                    />
                  </div>
                  <div className="space-y-3">
                    <label className="block text-sm font-bold text-gray-800">最大推送次数</label>
                    <input
                      type="number"
                      min={1}
                      max={20}
                      value={qrSettings.max_attempts}
                      onChange={e => setQrSettings({ ...qrSettings, max_attempts: Number(e.target.value) })}
                      className="w-full ios-input px-4 py-3 rounded-xl text-sm"
                    />
                  </div>
                </div>
              </div>

              <div className="space-y-3">
                <label className="block text-sm font-bold text-gray-800">目标账号</label>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {accounts.map(account => (
                    <button
                      type="button"
                      key={account.id}
                      onClick={() => toggleAccount(account.id)}
                      className={`p-3 rounded-xl text-left border transition-colors ${
                        qrSettings.account_ids.includes(account.id)
                          ? 'border-[#FFE815] bg-yellow-50'
                          : 'border-gray-100 bg-gray-50'
                      }`}
                    >
                      <div className="font-bold text-sm text-gray-900 truncate">{account.remark || account.nickname || account.id}</div>
                      <div className="text-xs text-gray-500 truncate">{account.id}</div>
                    </button>
                  ))}
                </div>
              </div>

              <div className="space-y-3">
                <label className="block text-sm font-bold text-gray-800">推送渠道</label>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {pushChannels.map(channel => (
                    <button
                      type="button"
                      key={channel.id}
                      onClick={() => toggleChannel(channel.id)}
                      className={`p-3 rounded-xl text-left border transition-colors ${
                        qrSettings.channel_ids.includes(Number(channel.id))
                          ? 'border-[#FFE815] bg-yellow-50'
                          : 'border-gray-100 bg-gray-50'
                      }`}
                    >
                      <div className="font-bold text-sm text-gray-900 truncate">{channel.name}</div>
                      <div className="text-xs text-gray-500">{channel.type}</div>
                    </button>
                  ))}
                </div>
              </div>

              <div className="flex flex-wrap gap-3">
                <button
                  type="button"
                  onClick={saveQRSettings}
                  disabled={qrSaving}
                  className="ios-btn-primary px-5 py-3 rounded-xl font-bold flex items-center gap-2 disabled:opacity-60"
                >
                  <Save className="w-4 h-4" />
                  {qrSaving ? '保存中...' : '保存扫码推送'}
                </button>
                <button
                  type="button"
                  onClick={testQRPush}
                  disabled={qrTesting}
                  className="px-5 py-3 bg-gray-900 text-white rounded-xl font-bold flex items-center gap-2 disabled:opacity-60"
                >
                  <Send className="w-4 h-4" />
                  {qrTesting ? '发送中...' : '测试推送'}
                </button>
              </div>
            </div>
          </section>
        </div>
      </div>

      <div className="fixed bottom-10 right-10 z-30">
        <button
          onClick={handleSave}
          disabled={saving}
          className="ios-btn-primary px-10 py-5 rounded-[2rem] text-lg shadow-2xl shadow-yellow-200 flex items-center gap-3 transform hover:scale-105 active:scale-95 transition-all disabled:opacity-70"
        >
          <Save className="w-6 h-6" />
          {saving ? '保存中...' : '保存系统配置'}
        </button>
      </div>
    </div>
  );
};

export default Settings;
