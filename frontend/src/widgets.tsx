// DealRoom's own AG Studio widgets. They read the same data engine as the built-in charts, so they follow the
// page's filters and cross-filters: click "Dispute open" in the status chart and "Needs you now" narrows to it.
//
//   Needs you now    one line per deal that is waiting on you, with the action and a link to the conversation
//   Proof on record  for each deal, the four facts DealRoom keeps (terms, PayPal payment, delivery, download),
//                    shown as stamps: the stronger the record, the safer the deal is in a dispute

import { useEffect, useState } from 'react';
import type { AgWidgetField, AgWidgetParams } from 'ag-studio';

type Row = Record<string, any>;

const AS = new URLSearchParams(location.search).get('as') || 'maya';

function field(params: AgWidgetParams, table: string, id: string): AgWidgetField | undefined {
  const schema = params.widgetApi.getSchema();
  const t = schema.find((x) => x.id === table);
  const key = (f: any) => String(f.id ?? f.fieldId ?? f.colId ?? '');
  return t?.fields.find((f) => key(f) === id || key(f) === `${table}.${id}` || key(f).endsWith(`.${id}`));
}

// Reads rows of `deals` through Studio, so filters apply. Keys of each row: the short field ids we asked for.
function useDeals(params: AgWidgetParams, ids: string[], queryId: string): Row[] | null {
  const [rows, setRows] = useState<Row[] | null>(null);
  useEffect(() => {
    let live = true;
    const api = params.widgetApi;
    const picked = ids.map((id) => [id, field(params, 'deals', id)] as const);
    if (picked.some(([, f]) => !f)) {
      api.setDisplayState('noData');
      return;
    }
    api.setDisplayState('loading');
    api
      .getData({ kind: 'flat', fields: picked.map(([, f]) => f!) }, { queryId } as any)
      .then((res) => {
        if (!live) return;
        const out = (res.results.rows || []).map((r) => {
          const o: Row = {};
          picked.forEach(([id, f], i) => {
            const k = String((f as any).id ?? (f as any).fieldId ?? '');
            o[id] = r[k] ?? r[id] ?? r[`deals.${id}`] ?? Object.values(r)[i];
          });
          return o;
        });
        setRows(out);
        api.setDisplayState(out.length ? 'displayed' : 'noData');
      })
      .catch(() => live && api.setDisplayState('noData'));
    return () => {
      live = false;
    };
  }, [params]);
  return rows;
}

const ORDER: Record<string, number> = { bad: 0, warn: 1, info: 2, good: 3 };

export function NeedsYou(params: AgWidgetParams) {
  const rows = useDeals(params, ['deal', 'counterpart', 'nextAction', 'nextLevel', 'roomId', 'owed'], 'needs-you');
  if (!rows) return null;
  const todo = rows.filter((r) => r.nextLevel !== 'good').sort((a, b) => (ORDER[a.nextLevel] ?? 9) - (ORDER[b.nextLevel] ?? 9));
  return (
    <div className="dr-needs">
      {todo.length === 0 && <p className="dr-empty">Nothing is waiting on you. Every deal in view is settled.</p>}
      <ul>
        {todo.map((r) => (
          <li key={r.roomId} className={`lvl-${r.nextLevel}`}>
            <span className="dr-dot" aria-hidden="true" />
            <span className="dr-what">
              <strong>{r.nextAction}</strong>
              <span>{r.deal} · {r.counterpart}</span>
            </span>
            <a className="dr-go" href={`/r/${r.roomId}?as=${AS}`}>Open</a>
          </li>
        ))}
      </ul>
    </div>
  );
}

const STAMPS: [string, string][] = [
  ['hasTerms', 'Terms'],
  ['hasPayment', 'PayPal'],
  ['hasDelivery', 'Delivered'],
  ['hasDownload', 'Downloaded'],
];

export function ProofOnRecord(params: AgWidgetParams) {
  const rows = useDeals(params, ['deal', 'counterpart', 'roomId', 'status', ...STAMPS.map(([k]) => k)], 'proof');
  if (!rows) return null;
  const truthy = (v: any) => v === true || v === 1 || v === 'true' || v === 'Yes';
  const full = rows.filter((r) => STAMPS.every(([k]) => truthy(r[k]))).length;
  return (
    <div className="dr-proof">
      <p className="dr-sum">
        <strong>{full} of {rows.length}</strong> deals have the full record a dispute needs.
      </p>
      <table>
        <thead>
          <tr><th>Deal</th>{STAMPS.map(([, label]) => <th key={label}>{label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.roomId} className={r.status === 'Dispute open' ? 'dispute' : ''}>
              <td><a href={`/r/${r.roomId}?as=${AS}`}>{r.deal}</a><span>{r.counterpart}</span></td>
              {STAMPS.map(([k, label]) => (
                <td key={k}>
                  <span className={`dr-stamp ${truthy(r[k]) ? 'on' : ''}`} title={`${label}: ${truthy(r[k]) ? 'on the record' : 'not yet'}`} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const form = (p: any) => p.createDefaults({});

export const customWidgets = [
  {
    id: 'needs-you',
    label: 'Needs you now',
    comp: NeedsYou,
    form,
    defaultSize: { width: 480, height: 320 },
    minSize: { width: 280, height: 160 },
    ai: { description: 'A to-do list: every deal waiting on the signed-in person, most urgent first, with a link to its conversation.' },
  },
  {
    id: 'proof-on-record',
    label: 'Proof on record',
    comp: ProofOnRecord,
    form,
    defaultSize: { width: 480, height: 320 },
    minSize: { width: 320, height: 180 },
    ai: { description: 'For each deal, which of the four facts DealRoom records are in place: accepted terms, PayPal payment, delivery, download.' },
  },
];
