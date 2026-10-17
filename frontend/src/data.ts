// AG Studio data sources for the dashboard: the signed-in person's deals, payments and alerts, as read from
// DealRoom's record by /api/dash, with the relationships between them and a shared calendar.

import type { AgDataSourcesDefinition, AgFieldDefinition, AgFormat } from 'ag-studio';

export type Deal = {
  id: string; roomId: number; deal: string; counterpart: string; status: string; price: number | null; paid: number;
  owed: number; started: string; lastActivity: string; hasTerms: boolean; hasPayment: boolean; hasDelivery: boolean;
  hasDownload: boolean; fileCheck: string; nextAction: string; nextLevel: string; aiPlayed: boolean;
  openIssue: number; needsYou: number; deals: number;
};
export type Payment = { id: string; dealId: string; counterpart: string; part: string; amount: number; date: string; source: string };
export type Alert = { id: string; dealId: string; counterpart: string; kind: string; level: string; date: string; text: string; isScam: number };
export type DashData = {
  me: { key: string; name: string; role: 'freelancer' | 'client' };
  deals: Deal[]; payments: Payment[]; alerts: Alert[]; license: string; ai: boolean;
};

type Spec = [id: string, name: string, format: AgFormat, hide?: boolean];
const USD = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
const fields = (specs: Spec[]): AgFieldDefinition[] =>
  specs.map(([id, name, format, hide]) => ({
    id, name, format, ...(hide && { hide }), ...(format === 'currencyFormat' && { formatOptions: { format: USD } }),
  }) as AgFieldDefinition);

export function buildData(d: DashData): AgDataSourcesDefinition {
  const seller = d.me.role === 'freelancer';
  const who = seller ? 'Client' : 'Freelancer';
  const dates = [...d.payments.map((p) => p.date), ...d.deals.map((x) => x.started)].filter(Boolean).sort();
  const today = new Date().toISOString().slice(0, 10);
  return {
    description:
      `DealRoom record for ${d.me.name}, a ${d.me.role}. Each deal is one job between ${d.me.name} and one ${who.toLowerCase()}: ` +
      'terms both accepted, PayPal payments (deposit and final, or full), the delivered file checked by NVIDIA Nemotron against ' +
      'the terms, the download, and any dispute. Amounts are USD. "PayPal sample" payments are recorded demo history; ' +
      '"PayPal sandbox" payments were made live in this demo.',
    sources: [
      {
        id: 'deals',
        description: `One row per deal (job). Owed is what is still due under the accepted terms. Next action is what the deal needs from ${d.me.name} now.`,
        data: d.deals,
        fields: fields([
          ['id', 'Deal ID', 'textFormat', true],
          ['roomId', 'Conversation', 'integerFormat'],
          ['deal', 'Deal', 'textFormat'],
          ['counterpart', who, 'textFormat'],
          ['status', 'Status', 'textFormat'],
          ['price', 'Price', 'currencyFormat'],
          ['paid', 'Paid', 'currencyFormat'],
          ['owed', seller ? 'Owed to you' : 'You still owe', 'currencyFormat'],
          ['started', 'Started', 'dateFormat'],
          ['lastActivity', 'Last activity', 'dateFormat'],
          ['hasTerms', 'Terms accepted', 'booleanFormat'],
          ['hasPayment', 'Paid through PayPal', 'booleanFormat'],
          ['hasDelivery', 'File delivered', 'booleanFormat'],
          ['hasDownload', 'Download recorded', 'booleanFormat'],
          ['fileCheck', 'File check', 'textFormat'],
          ['nextAction', 'Next action', 'textFormat'],
          ['nextLevel', 'Urgency', 'textFormat'],
          ['aiPlayed', 'Played by AI', 'booleanFormat', true],
          ['openIssue', 'Open issues', 'integerFormat'],
          ['needsYou', 'Needs you', 'integerFormat'],
          ['deals', 'Deals', 'integerFormat'],
        ]),
      },
      {
        id: 'payments',
        description: 'PayPal payments received (freelancer) or made (client), one row per captured payment.',
        data: d.payments,
        fields: fields([
          ['id', 'Payment ID', 'textFormat', true],
          ['dealId', 'Deal ID', 'textFormat', true],
          ['counterpart', seller ? 'Paid by' : 'Paid to', 'textFormat'],
          ['part', 'Payment part', 'textFormat'],
          ['amount', seller ? 'Earned' : 'Spent', 'currencyFormat'],
          ['date', 'Payment date', 'dateFormat'],
          ['source', 'Source', 'textFormat'],
        ]),
      },
      {
        id: 'alerts',
        description: 'What DealRoom flagged: scam warnings, honesty checks, file checks, disputes and payments.',
        data: d.alerts,
        fields: fields([
          ['id', 'Alert ID', 'textFormat', true],
          ['dealId', 'Deal ID', 'textFormat', true],
          ['counterpart', 'Alert with', 'textFormat'],
          ['kind', 'Alert kind', 'textFormat'],
          ['level', 'Alert level', 'textFormat'],
          ['date', 'Alert date', 'dateFormat'],
          ['text', 'Alert', 'textFormat'],
          ['isScam', 'Scam attempts flagged', 'integerFormat'],
        ]),
      },
    ],
    relationships: [
      { id: 'payment-deal', source: { tableId: 'payments', fieldId: 'dealId' }, target: { tableId: 'deals', fieldId: 'id' }, type: 'many-to-one' },
      { id: 'alert-deal', source: { tableId: 'alerts', fieldId: 'dealId' }, target: { tableId: 'deals', fieldId: 'id' }, type: 'many-to-one' },
      { id: 'payments-calendar', source: { tableId: 'payments', fieldId: 'date' }, target: { calendarId: 'calendar' } },
    ],
    calendars: [
      {
        id: 'calendar',
        label: 'Calendar',
        range: { from: { type: 'date', value: dates[0] ?? today }, to: { type: 'date', value: today } },
        fragments: ['year', 'quarter', 'month', 'week', 'day'],
      },
    ],
  };
}
