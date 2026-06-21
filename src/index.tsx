import {
  ButtonItem,
  DropdownItem,
  PanelSection,
  PanelSectionRow,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin, toaster } from "@decky/api";
import { useEffect, useMemo, useState } from "react";
import { FaMemory } from "react-icons/fa";

type MemoryInfo = {
  total_bytes: number;
  available_bytes: number;
  max_game_bytes: number;
};

type SteamGame = {
  appid: string;
  name: string;
  install_dir: string;
  library_path: string;
  size_on_disk: number;
};

type ActiveMove = {
  appid: string;
  name: string;
  original_library_path: string;
  original_install_dir: string;
  ramdisk_library_path: string;
  ramdisk_mount_path: string;
  size_on_disk: number;
};

type GameListResponse = {
  memory: MemoryInfo;
  games: SteamGame[];
};

type StatusResponse = {
  memory: MemoryInfo;
  active_move: ActiveMove | null;
};

type OperationResult = {
  ok: boolean;
  message: string;
  kind: string;
  details: Record<string, unknown>;
};

const getStatus = callable<[], StatusResponse>("get_status");
const listGames = callable<[], GameListResponse>("list_games");
const stageGame = callable<[appid: string, dry_run: boolean], OperationResult>("stage_game");
const revert = callable<[dry_run: boolean], OperationResult>("revert");

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0 B";
  }
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function Content() {
  const [memory, setMemory] = useState<MemoryInfo | null>(null);
  const [games, setGames] = useState<SteamGame[]>([]);
  const [activeMove, setActiveMove] = useState<ActiveMove | null>(null);
  const [selectedAppId, setSelectedAppId] = useState<string | undefined>();
  const [busy, setBusy] = useState(false);
  const [lastMessage, setLastMessage] = useState<string>("Ready.");

  const selectedGame = useMemo(
    () => games.find((game) => game.appid === selectedAppId),
    [games, selectedAppId],
  );

  const refresh = async () => {
    setBusy(true);
    try {
      const [status, gameList] = await Promise.all([getStatus(), listGames()]);
      setMemory(gameList.memory ?? status.memory);
      setGames(gameList.games);
      setActiveMove(status.active_move);
      setSelectedAppId((current) => current ?? gameList.games[0]?.appid);
      setLastMessage(`Found ${gameList.games.length} eligible installed games.`);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setLastMessage(message);
      toaster.toast({ title: "Decky RAMDisk scan failed", body: message });
    } finally {
      setBusy(false);
    }
  };

  const runOperation = async (operation: () => Promise<OperationResult>) => {
    setBusy(true);
    try {
      const result = await operation();
      setLastMessage(result.message);
      toaster.toast({
        title: result.ok ? "Decky RAMDisk" : "Decky RAMDisk needs attention",
        body: result.message,
      });
      await refresh();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setLastMessage(message);
      toaster.toast({ title: "Decky RAMDisk error", body: message });
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  return (
    <div>
      <PanelSection title="Memory Budget">
        <PanelSectionRow>
          <div className={staticClasses.PanelSectionRow}>
            Total RAM: {memory ? formatBytes(memory.total_bytes) : "Unknown"}
            <br />
            Eligible game limit: {memory ? formatBytes(memory.max_game_bytes) : "Unknown"}
          </div>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Game">
        <PanelSectionRow>
          <DropdownItem
            label="Installed game"
            rgOptions={games.map((game) => ({
              data: game.appid,
              label: `${game.name} (${formatBytes(game.size_on_disk)})`,
            }))}
            selectedOption={selectedAppId}
            onChange={(option) => setSelectedAppId(String(option.data))}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={refresh}>
            Scan Installed Games
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busy || !selectedGame || Boolean(activeMove)}
            onClick={() => selectedGame && runOperation(() => stageGame(selectedGame.appid, true))}
          >
            Preview RAM-Disk Move
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busy || !selectedGame || Boolean(activeMove)}
            onClick={() => selectedGame && runOperation(() => stageGame(selectedGame.appid, false))}
          >
            Move Selected Game
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Restore">
        <PanelSectionRow>
          <div className={staticClasses.PanelSectionRow}>
            {activeMove
              ? `${activeMove.name} is staged from ${activeMove.original_library_path}`
              : "No active RAM-disk move is recorded."}
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busy || !activeMove}
            onClick={() => runOperation(() => revert(false))}
          >
            Revert Active Move
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Status">
        <PanelSectionRow>
          <div className={staticClasses.PanelSectionRow}>{lastMessage}</div>
        </PanelSectionRow>
      </PanelSection>
    </div>
  );
}

export default definePlugin(() => ({
  name: "Decky RAMDisk",
  titleView: <div className={staticClasses.Title}>Decky RAMDisk</div>,
  content: <Content />,
  icon: <FaMemory />,
  onDismount() {
    console.log("Decky RAMDisk unloaded");
  },
}));
