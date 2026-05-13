import './nezuko.css';
import { useState } from 'react';
import { Home, ShoppingBag, Zap, Trophy, User, Star, ChevronRight, Flame, Gift } from 'lucide-react';

const PACKS = [
  { id: 1, name: '60 UC', price: '₴29', badge: null, hot: false, rarity: 'common' },
  { id: 2, name: '325 UC', price: '₴109', badge: 'ХІТ', hot: true, rarity: 'rare' },
  { id: 3, name: '660 UC', price: '₴209', badge: null, hot: false, rarity: 'rare' },
  { id: 4, name: '1800 UC', price: '₴549', badge: 'ТОП', hot: true, rarity: 'epic' },
  { id: 5, name: '3850 UC', price: '₴1099', badge: null, hot: false, rarity: 'epic' },
  { id: 6, name: '8100 UC', price: '₴2099', badge: '🔥', hot: false, rarity: 'legendary' },
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

const NAV = [
  { icon: Home,        label: 'Головна' },
  { icon: ShoppingBag, label: 'Магазин' },
  { icon: Zap,         label: 'Рулетка' },
  { icon: Trophy,      label: 'Топ' },
  { icon: User,        label: 'Профіль' },
];

export function NezukoApp() {
  const [activeTab, setActiveTab] = useState(0);

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
        {/* Sakura petals */}
        <div className="nz-petals" aria-hidden>
          {[...Array(6)].map((_,i) => <span key={i} className={`nz-petal nz-petal-${i}`}>🌸</span>)}
        </div>
      </header>

      {/* ── CONTENT ────────────────────────────────── */}
      <main className="nz-main">

        {/* Hero banner */}
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

        {/* Category pills */}
        <div className="nz-section-label">Категорії</div>
        <div className="nz-cats">
          {['UC', 'Prime', 'Prime +', 'Рулетка'].map((c, i) => (
            <button key={c} className={`nz-cat ${i === 0 ? 'nz-cat-active' : ''}`}>{c}</button>
          ))}
        </div>

        {/* Popular packs */}
        <div className="nz-section-row">
          <span className="nz-section-label" style={{margin:0}}>Популярні пакети</span>
          <button className="nz-see-all">Всі <ChevronRight size={12}/></button>
        </div>
        <div className="nz-packs">
          {PACKS.map(p => (
            <div
              key={p.id}
              className="nz-pack"
              style={{
                background: rarityGradient[p.rarity],
                borderColor: rarityBorder[p.rarity],
                boxShadow: rarityGlow[p.rarity],
              }}
            >
              {p.badge && <span className="nz-pack-badge">{p.badge}</span>}
              {p.hot && <Flame size={12} className="nz-pack-hot" />}
              <div className="nz-pack-icon">💎</div>
              <div className="nz-pack-name">{p.name}</div>
              <div className="nz-pack-price" style={{ color: rarityBorder[p.rarity] }}>{p.price}</div>
              <button className="nz-pack-btn">Купити</button>
            </div>
          ))}
        </div>

        {/* Quick actions */}
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
          <button className="nz-quick-card nz-qc-top">
            <Trophy size={22} />
            <span>Топ</span>
            <span className="nz-qc-sub">Рейтинг</span>
          </button>
        </div>

        {/* Promo banner */}
        <div className="nz-promo">
          <div className="nz-promo-left">
            <div className="nz-promo-title">🎁 Бонусні очки</div>
            <div className="nz-promo-desc">За кожне замовлення — накопичуй і витрачай</div>
            <button className="nz-promo-btn">Дізнатись більше</button>
          </div>
          <div className="nz-promo-right">✨</div>
        </div>

        <div style={{height:'8px'}} />
      </main>

      {/* ── BOTTOM NAV ────────────────────────────── */}
      <nav className="nz-nav">
        {NAV.map((item, i) => (
          <button
            key={i}
            className={`nz-nav-item ${activeTab === i ? 'nz-nav-active' : ''}`}
            onClick={() => setActiveTab(i)}
          >
            <item.icon size={20} />
            <span>{item.label}</span>
            {activeTab === i && <span className="nz-nav-pip" />}
          </button>
        ))}
      </nav>
    </div>
  );
}
