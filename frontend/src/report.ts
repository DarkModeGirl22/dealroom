// The two built-in reports: "My business" for the freelancer and "My purchases" for the client, as AG Studio
// report state. Field ids ('deals.owed') refer to the data sources in ./data.ts. Canvas: 24 tracks wide.

import type { AgPageState, AgReportState, AgWidgetLayoutState } from 'ag-studio';

const at = (xTrack: number, yTrack: number, xSpan: number, ySpan: number): AgWidgetLayoutState => ({ xTrack, yTrack, xSpan, ySpan });
const title = (text: string) => ({ title: { enabled: true, text } });
const sum = (id: string) => ({ id, aggregation: 'sum' as const });
const count = (id: string) => ({ id, aggregation: 'countd' as const });
const FONT = '"Instrument Sans", system-ui, sans-serif';
const type = (fontSize: number, fontWeight: 'normal' | 'bold' = 'normal') => ({ fontFamily: FONT, fontSize, fontWeight, fontStyle: 'normal' as const });

const kpi = (text: string, value: { id: string; aggregation?: 'sum' | 'countd' }, sparklineX?: string) => ({
  type: 'value' as const,
  dataMapping: { value: [value], ...(sparklineX && { sparklineX: [{ id: sparklineX }] }) },
  format: {
    title: { enabled: true, text, typography: type(13, 'bold') },
    caption: { enabled: true, text: '', typography: type(0) },
    style: { typography: type(26, 'bold') },
  },
});

const money = title;   // money charts take the first colour: green on the freelancer's side (Completed comes first)

const custom = (kind: 'needs-you' | 'proof-on-record', text: string) => ({ type: kind, format: title(text) }) as any;

function business(): AgPageState {
  return {
    id: 'business',
    widgets: {
      'kpi-earned': kpi('Earned through PayPal', sum('payments.amount'), 'calendar::month'),
      'kpi-owed': kpi('Owed to you', sum('deals.owed')),
      'kpi-issues': kpi('Open issues', sum('deals.openIssue')),
      'kpi-scams': kpi('Scam attempts flagged', sum('alerts.isScam')),
      'needs-you': custom('needs-you', 'Needs you now'),
      proof: custom('proof-on-record', 'Proof on record'),
      'by-month': {
        type: 'column-chart-grouped',
        dataMapping: { categoryKey: [{ id: 'calendar::month' }], valueKey: [sum('payments.amount')] },
        format: money('Earned by month'),
      },
      'by-status': {
        type: 'donut-chart',
        dataMapping: { categoryKey: [{ id: 'deals.status' }], valueKey: [sum('deals.deals')] },
        format: { ...title('Deals by status'), style: { theme: { common: { legend: { enabled: true } } } } },
      },
      'by-client': {
        type: 'bar-chart-grouped',
        dataMapping: { categoryKey: [{ id: 'deals.counterpart' }], valueKey: [sum('deals.paid')] },
        format: money('Earned by client'),
      },
      deals: {
        type: 'grid',
        dataMapping: {
          cols: [{ id: 'deals.deal' }, { id: 'deals.counterpart' }, { id: 'deals.status' }, { id: 'deals.price' },
                 { id: 'deals.paid' }, { id: 'deals.owed' }, { id: 'deals.fileCheck' }, { id: 'deals.lastActivity' }],
        },
        sort: [{ field: { id: 'deals.lastActivity' }, direction: 'desc' }],
        format: title('All deals'),
      },
    },
    widgetLayout: {
      'kpi-earned': at(0, 0, 6, 7), 'kpi-owed': at(6, 0, 6, 7), 'kpi-issues': at(12, 0, 6, 7), 'kpi-scams': at(18, 0, 6, 7),
      'needs-you': at(0, 7, 12, 17), proof: at(12, 7, 12, 17),
      'by-month': at(0, 24, 10, 16), 'by-status': at(10, 24, 7, 16), 'by-client': at(17, 24, 7, 16),
      deals: at(0, 40, 24, 20),
    },
  } as AgPageState;
}

function purchases(): AgPageState {
  return {
    id: 'purchases',
    widgets: {
      'kpi-spent': kpi('Paid through PayPal', sum('payments.amount')),
      'kpi-owe': kpi('You still owe', sum('deals.owed')),
      'kpi-issues': kpi('Open issues', sum('deals.openIssue')),
      'kpi-scams': kpi('Scam attempts flagged', sum('alerts.isScam')),
      'needs-you': custom('needs-you', 'Needs you now'),
      proof: custom('proof-on-record', 'Your protection, deal by deal'),
      'payments': {
        type: 'grid',
        dataMapping: { cols: [{ id: 'payments.date' }, { id: 'payments.counterpart' }, { id: 'payments.part' },
                              { id: 'payments.amount' }, { id: 'payments.source' }] },
        sort: [{ field: { id: 'payments.date' }, direction: 'desc' }],
        format: title('PayPal payments'),
      },
      'by-status': {
        type: 'donut-chart',
        dataMapping: { categoryKey: [{ id: 'deals.status' }], valueKey: [sum('deals.deals')] },
        format: { ...title('Deals by status'), style: { theme: { common: { legend: { enabled: true } } } } },
      },
      deals: {
        type: 'grid',
        dataMapping: {
          cols: [{ id: 'deals.deal' }, { id: 'deals.counterpart' }, { id: 'deals.status' }, { id: 'deals.price' },
                 { id: 'deals.paid' }, { id: 'deals.owed' }, { id: 'deals.fileCheck' }, { id: 'deals.lastActivity' }],
        },
        sort: [{ field: { id: 'deals.lastActivity' }, direction: 'desc' }],
        format: title('All purchases'),
      },
    },
    widgetLayout: {
      'kpi-spent': at(0, 0, 6, 7), 'kpi-owe': at(6, 0, 6, 7), 'kpi-issues': at(12, 0, 6, 7), 'kpi-scams': at(18, 0, 6, 7),
      'needs-you': at(0, 7, 12, 17), proof: at(12, 7, 12, 17),
      'payments': at(0, 24, 14, 16), 'by-status': at(14, 24, 10, 16),
      deals: at(0, 40, 24, 16),
    },
  } as AgPageState;
}

export const reportFor = (role: 'freelancer' | 'client'): AgReportState => {
  const page = role === 'freelancer' ? business() : purchases();
  return { selectedPageId: page.id, pages: [page] } as AgReportState;
};

// Use the unused helper so the build stays lint-clean if a count widget is added later.
export const _count = count;
