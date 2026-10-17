// The dashboard page: one AG Studio instance over the signed-in person's record, with DealRoom's own widgets,
// DealRoom's colours, and an AI assistant answered by NVIDIA Nemotron.

import { StrictMode, useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { AgStudioAiModule, createAiHarness, directLlmRunner, studioTheme, type AgAiHarnessSetup } from 'ag-studio';
import { AgStudio, AgStudioProvider, createWidgets } from 'ag-studio-react';
import { buildData, type DashData } from './data';
import { reportFor } from './report';
import { customWidgets } from './widgets';
import { openaiAdapter } from './openai-adapter';
import './dash.css';

const root = document.getElementById('dash-root')!;
const AS = root.dataset.as || 'maya';

// DealRoom's palette (see dealroom/static/style.css): ink, blue for actions, green for proof, amber, coral.
const C = { ink: '#14213D', ink2: '#3B4763', muted: '#69738A', line: '#E2E6EF', paper: '#F5F7FB', blue: '#2546D6', soft: '#EAEFFE' };
// in the order of dash.py's statuses: Completed, Delivered/payment due, Paid, Awaiting payment, Terms to accept,
// Talking, Doesn't match, Dispute. Single-series charts take the first colour: green, money that arrived.
const STATUS: [string, string][] = [
  ['Completed', '#0E8A6A'], ['Delivered, payment due', '#E3A21A'], ['Paid, in progress', '#2546D6'],
  ['Awaiting payment', '#F2C56B'], ['Terms to accept', '#7A8FE8'], ['Talking', '#9AA6C2'],
  ["Doesn't match the terms", '#E07A6F'], ['Dispute open', '#C93A2F'],
];
// Charts colour categories in the order they appear, so the palette follows the statuses this person has
// (dash.py sends deals in this order): every status keeps its colour, and a dispute is always coral.
function makeTheme(statuses: string[]) {
  const present = STATUS.filter(([s]) => statuses.includes(s)).map(([, c]) => c);
  const series = [...present, ...['#1A9BC7', '#7A5AF8', '#5BB896', '#4F6BED']].slice(0, 10);
  const palette = Object.fromEntries(series.flatMap((c, i) => [[`chartPaletteFills${i + 1}Color`, c], [`chartPaletteStrokes${i + 1}Color`, c]]));
  return studioTheme.withParams({
  fontFamily: '"Instrument Sans", system-ui, sans-serif',
  fontSize: 14,
  textColor: C.ink,
  foregroundColor: C.ink,
  subtleTextColor: C.muted,
  backgroundColor: '#FFFFFF',
  borderColor: C.line,
  accentColor: C.blue,
  iconColor: C.ink2,
  rowHoverColor: C.soft,
  studioWrapperBorder: false,
  studioWrapperBorderRadius: 0,
  studioWrapperSpacing: 0,
  studioWrapperBackgroundColor: C.paper,
  studioCanvasBorder: false,
  studioCanvasBorderRadius: 0,
  studioCanvasShadow: false,
  studioCanvasBackgroundColor: C.paper,
  studioCanvasPadding: 16,
  studioCanvasRowHeight: 18,
  studioWidgetBackgroundColor: '#FFFFFF',
  studioWidgetBorder: { width: 1, color: C.line },
  studioWidgetBorderRadius: 12,
  studioWidgetPadding: 12,
  studioWidgetTitleFontSize: 15,
  studioWidgetTitleFontWeight: 600,
  studioWidgetTitleTextColor: C.ink,
  studioWidgetSubtitleTextColor: C.muted,
  gridHeaderBackgroundColor: '#FFFFFF',
  gridHeaderTextColor: C.muted,
  gridHeaderFontWeight: 600,
  gridRowHeight: 38,
  gridRowBorder: { width: 1, color: '#EEF1F6' },
  gridWrapperBorder: false,
  chartTextColor: C.ink,
  chartSubtleTextColor: C.muted,
  chartAxisLineColor: C.line,
  chartGridLineColor: '#EEF1F6',
  ...palette,
} as any);
}

// AG Studio's five agents (lead, data, page, planning, widget), all running on NVIDIA Nemotron 3 Super, with
// DealRoom's rules added to the lead agent: answer in words first, never break the widgets people rely on.
function buildAi(me: DashData['me']): AgAiHarnessSetup {
  const seller = me.role === 'freelancer';
  const adapter = openaiAdapter({ endpoint: '/api/ai', model: 'nvidia/nemotron-3-super-120b-a12b' });
  const rules =
    `\n\n## DealRoom\nThe user is ${me.name}, a ${me.role} on DealRoom. Amounts are US dollars.\n` +
    '- When the user asks a question, answer it in plain words first, with the numbers the data agent found.\n' +
    '- Change the dashboard only when the user asks to show, add or chart something. Then ADD a new widget: never ' +
    'modify or remove the existing widgets (the KPI tiles, "Needs you now", "Proof on record", the charts and the grid).\n' +
    '- Never apply a filter that leaves a widget empty. Keep titles short and plain.\n';
  const promptStarters = seller
    ? [
        { label: 'Who owes me money?', prompt: 'Which clients still owe me money, and how much?' },
        { label: 'Which deals are at risk?', prompt: 'Which deals have an open issue or an incomplete record, and why is each one at risk?' },
        { label: 'Chart my best clients', prompt: 'Add a new chart of what I earned by client, largest first.' },
      ]
    : [
        { label: 'What do I still owe?', prompt: 'What do I still owe, and to whom?' },
        { label: 'Anything wrong?', prompt: 'Which of my purchases has a problem, and what should I do about it?' },
      ];
  return ({ api }) =>
    createAiHarness(api, ({ builtIn }) => {
      const lead: any = builtIn.lead;
      const defs: any[] = [
        { ...lead, instructions: (ctx: any, params: any) => (lead.instructions ? lead.instructions(ctx, params) : '') + rules },
        builtIn.data, builtIn.page, builtIn.planning, builtIn.widget,
      ];
      return { agents: defs.map((d) => directLlmRunner({ ...d, adapter })), primary: 'lead', promptStarters };
    });
}

function Dashboard() {
  const [data, setData] = useState<DashData | null>(null);
  const [asking, setAsking] = useState(false);   // the assistant lives in Studio's edit mode, beside the canvas
  const [error, setError] = useState('');
  useEffect(() => {
    fetch(`/api/dash?as=${AS}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then(setData)
      .catch(() => setError('Your private copy has expired. Log in again from the home page.'));
  }, []);
  const studioData = useMemo(() => (data ? buildData(data) : undefined), [data]);
  const widgets = useMemo(
    () => (config: any) => createWidgets({ additionalTypes: customWidgets, menu: [...config.menu, { label: 'DealRoom', widgetIds: customWidgets.map((w) => w.id) }] } as any),
    [],
  );
  if (error) return <p className="dash-msg">{error}</p>;
  if (!data || !studioData) return <p className="dash-msg">Loading your record…</p>;
  const seller = data.me.role === 'freelancer';
  const ai = data.ai ? buildAi(data.me) : undefined;
  return (
    <div className="dash">
      <header className="dash-head">
        <div>
          <h1>{seller ? 'My business' : 'My purchases'}</h1>
          <p>
            {seller ? 'Every deal, payment and alert from your conversations, straight from the record.' : 'Every purchase, payment and alert from your conversations, straight from the record.'}{' '}
            {data.ai ? 'Ask the assistant (NVIDIA Nemotron) to explain or add a view.' : ''}
          </p>
        </div>
        <div className="dash-actions">
          {data.ai && (
            <button type="button" className={`ask ${asking ? 'on' : ''}`} onClick={() => setAsking(!asking)} aria-pressed={asking}>
              {asking ? 'Close the assistant' : 'Ask Nemotron'}
            </button>
          )}
          <span className="dash-note">Built with AG Studio · history marked "PayPal sample" is demo data</span>
        </div>
      </header>
      <div className="dash-studio">
        <AgStudioProvider licenseKey={data.license || undefined} modules={ai ? [AgStudioAiModule] : undefined}>
          <AgStudio
            style={{ height: '100%', width: '100%' }}
            theme={makeTheme(data.deals.map((d) => d.status))}
            layout={{ widgetBorderEnabled: true } as any}
            data={studioData}
            widgets={widgets as any}
            ai={ai}
            initialState={reportFor(data.me.role)}
            mode={asking ? 'edit' : 'view'}
            loadThemeGoogleFonts={false}
            panels={{ view: { right: [] }, edit: { right: ['ai'] } } as any}
          />
        </AgStudioProvider>
      </div>
    </div>
  );
}

createRoot(root).render(
  <StrictMode>
    <Dashboard />
  </StrictMode>,
);
