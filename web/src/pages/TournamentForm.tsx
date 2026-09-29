import { useEffect, useState } from "react";

import { useI18n, type MessageKey } from "../i18n";

export interface TournamentPresetView {
  players: number;
  starting_stack_bb: number;
  hands_per_level: number;
  first_level: { small_blind: number; big_blind: number; ante: number; hands: number };
  payout: number[];
  pko: boolean;
  bounty_share: number;
  cash_share: number;
  buy_in: number;
  opponents: string;
  name: string;
}

export interface TournamentChoice {
  tournament: Record<string, unknown>;
  opponents: string;
}

function payoutText(shares: number[]): string {
  return shares.map((share) => String(Math.round(share * 1000) / 10)).join("/");
}

/** Tournament settings for tournament presets (benchmarks and tournament training). */
export function TournamentForm({
  preset,
  fields,
  onChange,
}: {
  preset: TournamentPresetView;
  fields: string[];
  onChange: (choice: TournamentChoice | null) => void;
}) {
  const { t } = useI18n();
  const [enabled, setEnabled] = useState(false);
  const [players, setPlayers] = useState(String(preset.players));
  const [stack, setStack] = useState(String(preset.starting_stack_bb));
  const [handsPerLevel, setHandsPerLevel] = useState(String(preset.hands_per_level));
  const [ante, setAnte] = useState(preset.first_level.ante > 0);
  const [payout, setPayout] = useState(payoutText(preset.payout));
  const [pko, setPko] = useState(preset.pko);
  const [bountyShare, setBountyShare] = useState(String((preset.bounty_share || 0.5) * 100));
  const [cashShare, setCashShare] = useState(String((preset.cash_share || 0.5) * 100));
  const [levels, setLevels] = useState("");
  const [opponents, setOpponents] = useState(preset.opponents);

  useEffect(() => {
    setEnabled(false);
    setPlayers(String(preset.players));
    setStack(String(preset.starting_stack_bb));
    setHandsPerLevel(String(preset.hands_per_level));
    setAnte(preset.first_level.ante > 0);
    setPayout(payoutText(preset.payout));
    setPko(preset.pko);
    setBountyShare(String((preset.bounty_share || 0.5) * 100));
    setCashShare(String((preset.cash_share || 0.5) * 100));
    setLevels("");
    setOpponents(preset.opponents);
  }, [preset]);

  useEffect(() => {
    if (!enabled) {
      onChange(null);
      return;
    }
    const tournament: Record<string, unknown> = {
      players: Number(players),
      starting_stack_bb: Number(stack),
      hands_per_level: Number(handsPerLevel),
      ante,
      payout,
      pko,
      bounty_share: Number(bountyShare) / 100,
      cash_share: Number(cashShare) / 100,
    };
    const rows = levels
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => line.split(/[\s,/]+/).map(Number));
    if (rows.length > 0) {
      tournament.blind_levels = rows.map(([small, big, levelAnte, hands]) => ({
        small_blind: small,
        big_blind: big,
        ante: levelAnte ?? 0,
        hands: hands ?? Number(handsPerLevel),
      }));
    }
    onChange({ tournament, opponents });
  }, [enabled, players, stack, handsPerLevel, ante, payout, pko, bountyShare, cashShare, levels, opponents]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <label className="checkbox">
        <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
        {t("tournamentForm.customize")}
      </label>
      {enabled ? (
        <>
          <div className="settings-grid">
            <label>
              {t("tournamentForm.players")}
              <input type="number" min={2} max={6} value={players} onChange={(event) => setPlayers(event.target.value)} />
            </label>
            <label>
              {t("tournamentForm.stack")}
              <input type="number" min={1} step="any" value={stack} onChange={(event) => setStack(event.target.value)} />
            </label>
            <label>
              {t("tournamentForm.handsPerLevel")}
              <input
                type="number"
                min={1}
                value={handsPerLevel}
                onChange={(event) => setHandsPerLevel(event.target.value)}
              />
            </label>
            <label>
              {t("tournamentForm.payout")}
              <input value={payout} onChange={(event) => setPayout(event.target.value)} placeholder="65/35" />
            </label>
            <label className="checkbox">
              <input type="checkbox" checked={ante} onChange={(event) => setAnte(event.target.checked)} />
              {t("tournamentForm.ante")}
            </label>
            <label className="checkbox">
              <input type="checkbox" checked={pko} onChange={(event) => setPko(event.target.checked)} />
              {t("tournamentForm.pko")}
            </label>
            {pko ? (
              <>
                <label>
                  {t("tournamentForm.bountyShare")}
                  <input
                    type="number"
                    min={1}
                    max={99}
                    value={bountyShare}
                    onChange={(event) => setBountyShare(event.target.value)}
                  />
                </label>
                <label>
                  {t("tournamentForm.cashShare")}
                  <input
                    type="number"
                    min={0}
                    max={100}
                    value={cashShare}
                    onChange={(event) => setCashShare(event.target.value)}
                  />
                </label>
              </>
            ) : null}
            <label>
              {t("tournamentForm.opponents")}
              <select value={opponents} onChange={(event) => setOpponents(event.target.value)}>
                {fields.map((field) => (
                  <option key={field} value={field}>
                    {t(`field.${field}` as MessageKey)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label>
            {t("tournamentForm.levels")}
            <textarea
              rows={4}
              value={levels}
              onChange={(event) => setLevels(event.target.value)}
              placeholder={"10 20 20 10\n15 30 30 10\n20 40 40 10"}
            />
            <span className="muted small">{t("tournamentForm.levelsHelp")}</span>
          </label>
        </>
      ) : null}
    </>
  );
}
