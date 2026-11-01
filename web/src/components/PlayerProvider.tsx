import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, type ReactNode } from "react";

import { getSettings, type PlayerSettings } from "../lib/api";

/**
 * Read-only player settings shared by every file card. The default is "off" so a
 * component rendered outside the provider keeps the plain navigation behaviour.
 */
export const PlayerContext = createContext<PlayerSettings | null>(null);

export function PlayerProvider({ children }: { children: ReactNode }) {
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: ({ signal }) => getSettings(signal),
    staleTime: 60_000,
    retry: false,
  });
  return (
    <PlayerContext.Provider value={settings.data?.player ?? null}>
      {children}
    </PlayerContext.Provider>
  );
}

export function usePlayerSettings(): PlayerSettings | null {
  return useContext(PlayerContext);
}
