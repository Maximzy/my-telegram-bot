import './nezuko.css';
import { useState, useEffect } from 'react';
import { Home, ShoppingBag, Zap, Trophy, User, Star, ChevronRight, Flame, Gift, ScrollText, Heart, Shield } from 'lucide-react';

/* ── Gift images ─────────────────────────────────── */
import giftEaster   from '/gift_easter.png';
import giftApril    from '/gift_april.png';
import giftPatrick  from '/gift_patrick.png';
import giftMarch8   from '/gift_march8.png';
import giftValentine from '/gift_valentine.png';
import giftLoveU    from '/gift_loveu.png';
import giftXmasBear from '/gift_xmas_bear.png';
import giftXmasTree from '/gift_xmas_tree.png';

/* ── Data ────────────────────────────────────────── */
const PACKS = [
  { id: 1, name: '60 UC',   price: '₴40',   badge: null,  hot: false, rarity: 'common'    },
  { id: 2, name: '325 UC',  price: '₴195',  badge: 'ХІТ', hot: true,  rarity: 'rare'      },
  { id: 3, name: '660 UC',  price: '₴389',  badge: null,  hot: false, rarity: 'rare'      },
  { id: 4, name: '1800 UC', price: '₴960',  badge: 'ТОП', hot: true,  rarity: 'epic'      },
  { id: 5, name: '3800 UC', price: '₴1909', badge: null,  hot: false, rarity: 'epic'      },
  { id: 6, name: '8100 UC', price: '₴3840', badge: '🔥',  hot: false, rarity: 'legendary' },
];
const TG_GIFTS = [
  { id: 'easter',    name: '🐣 Пасхальний',      img: giftEaster,   price: '₴50' },
  { id: 'april',     name: '🎉 1 Квітня',         img: giftApril,    price: '₴50' },
  { id: 'patrick',   name: '🍀 Патрика',          img: giftPatrick,  price: '₴50' },
  { id: 'march8',    name: '🌸 8 Березня',        img: giftMarch8,   price: '₴50' },
  { id: 'valentine', name: '❤️ Валентина',        img: giftValentine, price: '₴50' },
  { id: 'loveu',     name: '💝 Серце Валентина',  img: giftLoveU,    price: '₴50' },
  { id: 'xmas_bear', name: '🧸 Новорічний',       img: giftXmasBear, price: '₴50' },
  { id: 'xmas_tree', name: '🎄 Ялинка Новорічна', img: giftXmasTree, price: '₴50' },
];
const DONATE_AMOUNTS = [20, 50, 100, 200, 500];

const rarityGradient: Record<string, string> = {
  common:    'linear-gradient(135deg,#1a1a2e,#16213e)',
  rare:      'linear-gradient(135deg,#0d1b2a,#1b2838)',
  epic:      'linear-gradient(135deg,#1a0533,#2d0a4e)',
  legendary: 'linear-gradient(135deg,#2b1200,#3d1a00)',
};
const rarityBorder: Record<string, string> = {
  common: '#334155', rare: '#3b82f6', epic: '#a855f7', legendary: '#f59e0b',
};
const rarityGlow: Record<string, string> = {
  common: 'none',
  rare:      '0 0 18px rgba(59,130,246,.25)',
  epic:      '0 0 20px rgba(168,85,247,.3)',
  legendary: '0 0 24px rgba(245,158,11,.35)',
};

const MOCK_LOGS = [
  { id: 12, action: 'ORDER_DONE',    detail: 'order=ORD-0012 pack=325 UC',      ts: '14:32' },
  { id: 11, action: 'ORDER_DONE',    detail: 'order=ORD-0011 pack=660 UC',      ts: '13:18' },
  { id: 10, action: 'ORDER_CANCELED',detail: 'order=ORD-0010 pack=60 UC',       ts: '12:05' },
  { id: 9,  action: 'DONATE_OK',     detail: 'don_id=3 amount=100',             ts: '11:44' },
  { id: 8,  action: 'ORDER_DONE',    detail: 'order=ORD-0009 pack=1800 UC',     ts: '10:21' },
  { id: 7,  action: 'LOGIN',         detail: '@admin_user',                      ts: '09:00' },
];
const MOCK_DONATIONS = [
  { id: 3, user: '@viper_ua',  amount: 100, method: 'card',  status: 'done'    },
  { id: 2, user: '@starlight', amount: 50,  method: 'stars', status: 'done'    },
  { id: 1, user: '@anon',      amount: 200, method: 'card',  status: 'pending' },
];
const ACTION_COLOR: Record<string, string> = {
  ORDER_DONE: '#4ade80', ORDER_CANCELED: '#f87171', DONATE_OK: '#f472b6', LOGIN: '#60a5fa',
};
const ACTION_ICON: Record<string, string> = {
  ORDER_DONE: '✅', ORDER_CANCELED: '❌', DONATE_OK: '💖', LOGIN: '🔐',
};

type Tab = 'home' | 'shop' | 'spin' | 'top' | 'logs';
type ShopCat = 'all' | 'pubg' | 'tg';

const NAV: { icon: React.ElementType; label: string; key: Tab }[] = [
  { icon: Home,       label: 'Головна', key: 'home' },
  { icon: ShoppingBag,label: 'Магазин', key: 'shop' },
  { icon: Zap,        label: 'Рулетка', key: 'spin' },
  { icon: Trophy,     label: 'Топ',     key: 'top'  },
  { icon: ScrollText, label: 'Логи',    key: 'logs' },
];

export function NezukoApp() {
  const [tab, setTab] = useState<Tab>('home');
  const [shopCat, setShopCat] = useState<ShopCat>('all');
  const [logTab, setLogTab] = useState<'actions'|'donations'>('actions');
  const [donateStep, setDonateStep] = useState<'amounts'|'method'|null>(null);
  const [donateAmount, setDonateAmount] = useState(0);
  const [isAdmin, setIsAdmin] = useState(false);

  /* Verify admin via init_data on mount */
  useEffect(() => {
    const tg = (window as any).Telegram?.WebApp;
    if (!tg?.initData) return;
    fetch('/api/check-admin', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ init_data: tg.initData }),
    })
      .then(r => r.json())
      .then(d => { if (d.ok && d.is_admin) setIsAdmin(true); })
      .catch(() => {});
  }, []);

  const goTab = (t: Tab) => { setTab(t); setDonateStep(null); };

  return (
    <div className="nezuko-root">

      {/* ── HEADER ───────────────────────────────── */}
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
            <div className="nz-online"><span className="nz-dot" /><span>14 онлайн</span></div>
            <div className="nz-pts-chip">
              <Star size={11} color="#ffd166" fill="#ffd166" /><span>320 pts</span>
            </div>
            {isAdmin && (
              <div className="nz-admin-badge" title="Адмін">
                <Shield size={13} /> ADM
              </div>
            )}
          </div>
        </div>
        <div className="nz-petals" aria-hidden>
          {[...Array(6)].map((_,i) => <span key={i} className={`nz-petal nz-petal-${i}`}>🌸</span>)}
        </div>
      </header>

      {/* ── CONTENT ──────────────────────────────── */}
      <main className="nz-main">

        {/* ════════ HOME ════════ */}
        {tab === 'home' && (<>
          <div className="nz-hero">
            <div className="nz-hero-glow" />
            <div className="nz-hero-content">
              <div className="nz-hero-label">PUBG Mobile</div>
              <div className="nz-hero-title">Поповнення<br /><span className="nz-accent">UC & Prime</span></div>
              <div className="nz-hero-desc">Швидко · Безпечно · 24/7</div>
              <button className="nz-btn-hero" onClick={() => goTab('shop')}>
                Купити зараз <ChevronRight size={16} />
              </button>
            </div>
            <div className="nz-hero-img">🎮</div>
          </div>

          {/* ── Category selector ── */}
          <div className="nz-section-label">Категорії</div>
          <div className="nz-cat-cards">
            <button className="nz-cat-card nz-cc-pubg" onClick={() => { goTab('shop'); setShopCat('pubg'); }}>
              <div className="nz-cc-bg nz-cc-bg-pubg" />
              <div className="nz-cc-body">
                <span className="nz-cc-emoji">🔫</span>
                <div className="nz-cc-title">PUBG Mobile</div>
                <div className="nz-cc-sub">UC · Prime · Підйом</div>
              </div>
              <div className="nz-cc-char">🌸⚔️</div>
            </button>
            <button className="nz-cat-card nz-cc-tg" onClick={() => { goTab('shop'); setShopCat('tg'); }}>
              <div className="nz-cc-bg nz-cc-bg-tg" />
              <div className="nz-cc-body">
                <span className="nz-cc-emoji">🧸</span>
                <div className="nz-cc-title">Telegram</div>
                <div className="nz-cc-sub">Старі подарки</div>
              </div>
              <div className="nz-cc-char">🌸🎀</div>
            </button>
          </div>

          {/* ── Quick packs ── */}
          <div className="nz-section-row">
            <span className="nz-section-label" style={{margin:0}}>Популярні пакети</span>
            <button className="nz-see-all" onClick={() => goTab('shop')}>Всі <ChevronRight size={12}/></button>
          </div>
          <div className="nz-packs">
            {PACKS.slice(0,4).map(p => (
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
            <button className="nz-quick-card nz-qc-spin" onClick={() => goTab('spin')}>
              <Zap size={22}/><span>Рулетка</span><span className="nz-qc-sub">Безкоштовно</span>
            </button>
            <button className="nz-quick-card nz-qc-orders">
              <Gift size={22}/><span>Замовлення</span><span className="nz-qc-sub">Переглянути</span>
            </button>
            <button className="nz-quick-card nz-qc-top" onClick={() => goTab('logs')}>
              <Heart size={22}/><span>Підтримати</span><span className="nz-qc-sub">Задонатити</span>
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

        {/* ════════ SHOP ════════ */}
        {tab === 'shop' && (<>
          {/* Category tabs */}
          <div className="nz-shop-tabs">
            {([['all','Всі 🌸'],['pubg','🔫 PUBG'],['tg','🧸 Telegram']] as [ShopCat,string][]).map(([k,l]) => (
              <button key={k} className={`nz-shop-tab ${shopCat===k?'nz-shop-tab-active':''}`}
                onClick={() => setShopCat(k)}>{l}</button>
            ))}
          </div>

          {/* PUBG section */}
          {(shopCat === 'all' || shopCat === 'pubg') && (<>
            <div className="nz-pubg-banner">
              <div className="nz-pubg-bg" />
              <div className="nz-pubg-content">
                <div className="nz-pubg-title">🔫 PUBG Mobile UC</div>
                <div className="nz-pubg-sub">Незуко стріляє по всіх! 🌸⚔️</div>
              </div>
            </div>
            <div className="nz-packs" style={{marginBottom:16}}>
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
          </>)}

          {/* Telegram Gifts section */}
          {(shopCat === 'all' || shopCat === 'tg') && (<>
            <div className="nz-tg-banner">
              <div className="nz-tg-bg" />
              <div className="nz-tg-content">
                <div className="nz-tg-title">🧸 Старі подарки Telegram</div>
                <div className="nz-tg-sub">Незуко тримає ведмедика 🌸🎀</div>
                <div className="nz-tg-price-note">Кожен подарунок — 50 грн</div>
              </div>
            </div>
            <div className="nz-gifts-grid">
              {TG_GIFTS.map(g => (
                <div key={g.id} className="nz-gift-card">
                  <img src={g.img} alt={g.name} className="nz-gift-img" />
                  <div className="nz-gift-name">{g.name}</div>
                  <div className="nz-gift-price">{g.price}</div>
                  <button className="nz-gift-btn">Купити</button>
                </div>
              ))}
            </div>
          </>)}
          <div style={{height:'8px'}} />
        </>)}

        {/* ════════ SPIN / TOP placeholder ════════ */}
        {(tab === 'spin' || tab === 'top') && (
          <div className="nz-placeholder">
            <div style={{fontSize:48,marginBottom:12}}>{tab==='spin'?'⚡':'🏆'}</div>
            <div style={{color:'var(--muted)',fontSize:14}}>{tab==='spin'?'Рулетка':'Топ гравців'}</div>
          </div>
        )}

        {/* ════════ LOGS / DONATE ════════ */}
        {tab === 'logs' && (<>
          {/* Donate block */}
          <div className="nz-donate-card">
            <div className="nz-donate-header"><Heart size={18} color="#ff2d78" fill="#ff2d78" /><span>Підтримати бота</span></div>
            <div className="nz-donate-desc">Допоможи розвитку магазину — обери суму</div>
            {(donateStep === null || donateStep === 'amounts') && (
              <div className="nz-donate-btns">
                {DONATE_AMOUNTS.map(a => (
                  <button key={a} className="nz-donate-amt"
                    onClick={() => { setDonateAmount(a); setDonateStep('method'); }}>₴{a}</button>
                ))}
                <button className="nz-donate-amt nz-donate-custom">✍️ Своя</button>
              </div>
            )}
            {donateStep === 'method' && (
              <div className="nz-donate-method">
                <div className="nz-donate-chosen">Сума: <b>₴{donateAmount}</b></div>
                <button className="nz-donate-way nz-dw-card">💳 Переказ на карту (UAH)</button>
                <button className="nz-donate-way nz-dw-stars">⭐ Telegram Stars ({Math.max(1, Math.round(donateAmount * 0.5))}⭐)</button>
                <button className="nz-donate-back" onClick={() => setDonateStep('amounts')}>← Назад</button>
              </div>
            )}
          </div>

          {/* Admin-only logs */}
          {isAdmin ? (<>
            <div className="nz-admin-panel-label">
              <Shield size={14} /> Адмін-панель
            </div>
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
                        {l.action.replace('_',' ')}
                      </div>
                      <div className="nz-log-detail">{l.detail}</div>
                    </div>
                    <div className="nz-log-ts">{l.ts}</div>
                  </div>
                ))}
              </div>
            )}
            {logTab === 'donations' && (
              <div className="nz-log-list">
                {MOCK_DONATIONS.map(d => (
                  <div key={d.id} className="nz-log-item">
                    <div className="nz-log-icon">{d.method==='card'?'💳':'⭐'}</div>
                    <div className="nz-log-body">
                      <div className="nz-log-action" style={{ color: d.status==='done'?'#4ade80':'#fbbf24' }}>{d.user}</div>
                      <div className="nz-log-detail">₴{d.amount} · {d.method} · {d.status}</div>
                    </div>
                    <div className="nz-log-ts">–</div>
                  </div>
                ))}
              </div>
            )}
          </>) : (
            <div className="nz-admin-locked">
              <Shield size={28} color="#334155" />
              <div>Адмін-панель доступна тільки через Telegram</div>
            </div>
          )}
          <div style={{height:'8px'}} />
        </>)}

      </main>

      {/* ── NAV ────────────────────────────────────── */}
      <nav className="nz-nav">
        {NAV.map(item => (
          <button key={item.key}
            className={`nz-nav-item ${tab===item.key?'nz-nav-active':''}`}
            onClick={() => goTab(item.key)}>
            <item.icon size={20} />
            <span>{item.label}</span>
            {tab===item.key && <span className="nz-nav-pip" />}
          </button>
        ))}
      </nav>
    </div>
  );
}
