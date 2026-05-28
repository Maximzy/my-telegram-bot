import './nezuko.css';
import { useState } from 'react';
import { Home, ShoppingBag, Zap, Trophy, User, Star, ChevronRight, Flame, Gift, ScrollText, Heart } from 'lucide-react';

const PACKS = [
  { id: 1, name: '60 UC', price: '₴40', badge: null, hot: false, rarity: 'common' },
  { id: 2, name: '325 UC', price: '₴195', badge: 'ХІТ', hot: true, rarity: 'rare' },
  { id: 3, name: '660 UC', price: '₴389', badge: null, hot: false, rarity: 'rare' },
  { id: 4, name: '1800 UC', price: '₴960', badge: 'ТОП', hot: true, rarity: 'epic' },
  { id: 5, name: '3800 UC', price: '₴1909', badge: null, hot: false, rarity: 'epic' },
  { id: 6, name: '8100 UC', price: '₴3840', badge: '🔥', hot: false, rarity: 'legendary' },
];

const rarityGradient: Record<string, string> = {
  common:    'linear-gradient(135deg,#1a1a2e,#16213e)',
  rare:      'linear-gradient(135deg,#0d1b2a,#1b2838)',
  epic:      'linear-gradient(135deg,#1a0533,#2d0a4e)',
  legendary: 'linear-gradient(135deg,#2b1200,#3d1a00)',
};
const rarityBorder: Record<string, string> = {
  common:    '#334155',
  rare:      '#3b82f6',
  epic:      '#a855f7',
  legendary: '#f59e0b',
};
const rarityGlow: Record<string, string> = {
  common:    'none',
  rare:      '0 0 18px rgba(59,130,246,.25)',
  epic:      '0 0 20px rgba(168,85,247,.3)',
  legendary: '0 0 24px rgba(245,158,11,.35)',
};

const MOCK_LOGS = [
  { id: 12, admin_id: 987654321, action: 'ORDER_DONE',    detail: 'order=ORD-0012 pack=325 UC user=112233', ts: '2026-05-28 14:32' },
  { id: 11, admin_id: 987654321, action: 'ORDER_DONE',    detail: 'order=ORD-0011 pack=660 UC user=445566', ts: '2026-05-28 13:18' },
  { id: 10, admin_id: 987654321, action: 'ORDER_CANCELED',detail: 'order=ORD-0010 pack=60 UC user=778899',  ts: '2026-05-28 12:05' },
  { id: 9,  admin_id: 987654321, action: 'DONATE_OK',     detail: 'don_id=3 amount=100 user=223344',        ts: '2026-05-28 11:44' },
  { id: 8,  admin_id: 987654321, action: 'ORDER_DONE',    detail: 'order=ORD-0009 pack=1800 UC user=334455',ts: '2026-05-28 10:21' },
  { id: 7,  admin_id: 987654321, action: 'LOGIN',         detail: '@admin_user',                            ts: '2026-05-28 09:00' },
  { id: 6,  admin_id: 987654321, action: 'ORDER_DONE',    detail: 'order=ORD-0006 pack=Prime user=556677', ts: '2026-05-27 22:14' },
  { id: 5,  admin_id: 987654321, action: 'ORDER_CANCELED',detail: 'order=ORD-0005 pack=30 UC user=667788', ts: '2026-05-27 19:02' },
];

const MOCK_DONATIONS = [
  { id: 3, user: '@viper_ua',   amount: 100, method: 'card',  status: 'done',      ts: '2026-05-28 11:40' },
  { id: 2, user: '@starlight',  amount: 50,  method: 'stars', status: 'done',      ts: '2026-05-27 18:55' },
  { id: 1, user: '@anon',       amount: 200, method: 'card',  status: 'pending',   ts: '2026-05-27 14:30' },
];

const ACTION_COLOR: Record<string, string> = {
  ORDER_DONE:     '#4ade80',
  ORDER_CANCELED: '#f87171',
  DONATE_OK:      '#f472b6',
  LOGIN:          '#60a5fa',
};
const ACTION_ICON: Record<string, string> = {
  ORDER_DONE:     '✅',
  ORDER_CANCELED: '❌',
  DONATE_OK:      '💖',
  LOGIN:          '🔐',
};

const DONATE_AMOUNTS = [20, 50, 100, 200, 500];

const NAV = [
  { icon: Home,        label: 'Головна' },
  { icon: ShoppingBag, label: 'Магазин' },
  { icon: Zap,         label: 'Рулетка' },
  { icon: Trophy,      label: 'Топ' },
  { icon: ScrollText,  label: 'Логи' },
];

export function NezukoApp() {
  const [activeTab, setActiveTab] = useState(0);
  const [logTab, setLogTab] = useState<'actions'|'donations'>('actions');
  const [donateStep, setDonateStep] = useState<'amounts'|'method'|null>(null);
  const [donateAmount, setDonateAmount] = useState(0);

  return (
    <div className="nezuko-root">
      {/* ── HEADER ─────────────────────────────────── */}
      <header className="nz-header">
        <div className="nz-header-inner">
          <div className="nz-logo">
            <span className="nz-logo-icon">🌸</span>
            <div>
              <div className="nz-logo-title">UC Shop</div>
              <div className="nz-logo-sub">Nezuko Store</div>
            </div>
          </div>
          <div className="nz-header-right">
            <div className="nz-online">
              <span className="nz-dot" />
              <span>14 онлайн</span>
            </div>
            <div className="nz-pts-chip">
              <Star size={11} color="#ffd166" fill="#ffd166" />
              <span>320 pts</span>
            </div>
          </div>
        </div>
        <div className="nz-petals" aria-hidden>
          {[...Array(6)].map((_,i) => <span key={i} className={`nz-petal nz-petal-${i}`}>🌸</span>)}
        </div>
      </header>

      {/* ── CONTENT ────────────────────────────────── */}
      <main className="nz-main">

        {/* ── HOME TAB ── */}
        {activeTab === 0 && (<>
          <div className="nz-hero">
            <div className="nz-hero-glow" />
            <div className="nz-hero-content">
              <div className="nz-hero-label">PUBG Mobile</div>
              <div className="nz-hero-title">Поповнення<br /><span className="nz-accent">UC & Prime</span></div>
              <div className="nz-hero-desc">Швидко · Безпечно · 24/7</div>
              <button className="nz-btn-hero">
                Купити зараз
                <ChevronRight size={16} />
              </button>
            </div>
            <div className="nz-hero-img">🎮</div>
          </div>

          <div className="nz-section-label">Категорії</div>
          <div className="nz-cats">
            {['UC', 'Prime', 'Prime +', 'Рулетка'].map((c, i) => (
              <button key={c} className={`nz-cat ${i === 0 ? 'nz-cat-active' : ''}`}>{c}</button>
            ))}
          </div>

          <div className="nz-section-row">
            <span className="nz-section-label" style={{margin:0}}>Популярні пакети</span>
            <button className="nz-see-all">Всі <ChevronRight size={12}/></button>
          </div>
          <div className="nz-packs">
            {PACKS.map(p => (
              <div key={p.id} className="nz-pack"
                style={{ background: rarityGradient[p.rarity], borderColor: rarityBorder[p.rarity], boxShadow: rarityGlow[p.rarity] }}>
                {p.badge && <span className="nz-pack-badge">{p.badge}</span>}
                {p.hot && <Flame size={12} className="nz-pack-hot" />}
                <div className="nz-pack-icon">💎</div>
                <div className="nz-pack-name">{p.name}</div>
                <div className="nz-pack-price" style={{ color: rarityBorder[p.rarity] }}>{p.price}</div>
                <button className="nz-pack-btn">Купити</button>
              </div>
            ))}
          </div>

          <div className="nz-section-label">Швидкі дії</div>
          <div className="nz-quick">
            <button className="nz-quick-card nz-qc-spin">
              <Zap size={22} />
              <span>Рулетка</span>
              <span className="nz-qc-sub">Безкоштовно</span>
            </button>
            <button className="nz-quick-card nz-qc-orders">
              <Gift size={22} />
              <span>Замовлення</span>
              <span className="nz-qc-sub">Переглянути</span>
            </button>
            <button className="nz-quick-card nz-qc-top" onClick={() => setActiveTab(4)}>
              <Heart size={22} />
              <span>Підтримка</span>
              <span className="nz-qc-sub">Задонатити</span>
            </button>
          </div>

          <div className="nz-promo">
            <div className="nz-promo-left">
              <div className="nz-promo-title">🎁 Бонусні очки</div>
              <div className="nz-promo-desc">За кожне замовлення — накопичуй і витрачай</div>
              <button className="nz-promo-btn">Дізнатись більше</button>
            </div>
            <div className="nz-promo-right">✨</div>
          </div>

          <div style={{height:'8px'}} />
        </>)}

        {/* ── LOGS TAB ── */}
        {activeTab === 4 && (<>
          {/* Donate block */}
          <div className="nz-donate-card">
            <div className="nz-donate-header">
              <Heart size={18} color="#ff2d78" fill="#ff2d78" />
              <span>Підтримати бота</span>
            </div>
            <div className="nz-donate-desc">Допоможи розвитку магазину — обери суму</div>

            {(donateStep === null || donateStep === 'amounts') && (
              <div className="nz-donate-btns">
                {DONATE_AMOUNTS.map(a => (
                  <button key={a} className="nz-donate-amt"
                    onClick={() => { setDonateAmount(a); setDonateStep('method'); }}>
                    ₴{a}
                  </button>
                ))}
                <button className="nz-donate-amt nz-donate-custom">✍️ Своя</button>
              </div>
            )}

            {donateStep === 'method' && (
              <div className="nz-donate-method">
                <div className="nz-donate-chosen">Сума: <b>₴{donateAmount}</b></div>
                <button className="nz-donate-way nz-dw-card">
                  💳 Переказ на карту (UAH)
                </button>
                <button className="nz-donate-way nz-dw-stars">
                  ⭐ Telegram Stars ({Math.max(1, Math.round(donateAmount * 0.5))}⭐)
                </button>
                <button className="nz-donate-back" onClick={() => setDonateStep('amounts')}>← Назад</button>
              </div>
            )}
          </div>

          {/* Logs block */}
          <div className="nz-log-tabs">
            <button className={`nz-log-tab ${logTab==='actions'?'nz-log-tab-active':''}`}
              onClick={() => setLogTab('actions')}>📋 Дії адміна</button>
            <button className={`nz-log-tab ${logTab==='donations'?'nz-log-tab-active':''}`}
              onClick={() => setLogTab('donations')}>💖 Донати</button>
          </div>

          {logTab === 'actions' && (
            <div className="nz-log-list">
              {MOCK_LOGS.map(l => (
                <div key={l.id} className="nz-log-item">
                  <div className="nz-log-icon">{ACTION_ICON[l.action] ?? '⚡'}</div>
                  <div className="nz-log-body">
                    <div className="nz-log-action" style={{ color: ACTION_COLOR[l.action] ?? '#94a3b8' }}>
                      {l.action.replace('_', ' ')}
                    </div>
                    <div className="nz-log-detail">{l.detail}</div>
                  </div>
                  <div className="nz-log-ts">{l.ts.slice(11)}</div>
                </div>
              ))}
            </div>
          )}

          {logTab === 'donations' && (
            <div className="nz-log-list">
              {MOCK_DONATIONS.map(d => (
                <div key={d.id} className="nz-log-item">
                  <div className="nz-log-icon">{d.method === 'card' ? '💳' : '⭐'}</div>
                  <div className="nz-log-body">
                    <div className="nz-log-action" style={{ color: d.status === 'done' ? '#4ade80' : '#fbbf24' }}>
                      {d.user}
                    </div>
                    <div className="nz-log-detail">₴{d.amount} · {d.method} · {d.status}</div>
                  </div>
                  <div className="nz-log-ts">{d.ts.slice(11)}</div>
                </div>
              ))}
            </div>
          )}

          <div style={{height:'8px'}} />
        </>)}

        {/* ── OTHER TABS placeholder ── */}
        {(activeTab === 1 || activeTab === 2 || activeTab === 3) && (
          <div className="nz-placeholder">
            <div style={{fontSize:48, marginBottom:12}}>
              {activeTab === 1 ? '🛍️' : activeTab === 2 ? '⚡' : '🏆'}
            </div>
            <div style={{color:'var(--muted)', fontSize:14}}>
              {activeTab === 1 ? 'Магазин' : activeTab === 2 ? 'Рулетка' : 'Топ гравців'}
            </div>
          </div>
        )}

      </main>

      {/* ── BOTTOM NAV ────────────────────────────── */}
      <nav className="nz-nav">
        {NAV.map((item, i) => (
          <button key={i}
            className={`nz-nav-item ${activeTab === i ? 'nz-nav-active' : ''}`}
            onClick={() => { setActiveTab(i); setDonateStep(null); }}>
            <item.icon size={20} />
            <span>{item.label}</span>
            {activeTab === i && <span className="nz-nav-pip" />}
          </button>
        ))}
      </nav>
    </div>
  );
}
