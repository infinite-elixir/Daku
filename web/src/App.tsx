import { Route, Routes } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { PlayerProvider } from "./components/PlayerProvider";
import { DownloadsPage } from "./pages/DownloadsPage";
import { HomePage } from "./pages/HomePage";
import { LibraryPage } from "./pages/LibraryPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { PreviewPage } from "./pages/PreviewPage";
import { SearchPage } from "./pages/SearchPage";
import { SettingsPage } from "./pages/SettingsPage";
import { TitleFilesPage } from "./pages/TitleFilesPage";
import { TitlePage } from "./pages/TitlePage";

export default function App() {
  return (
    <PlayerProvider>
      <AppShell>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/downloads" element={<DownloadsPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/preview/:kind/:source/:sourceId" element={<PreviewPage />} />
          <Route path="/title/:id" element={<TitlePage />} />
          <Route path="/title/:id/files" element={<TitleFilesPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </AppShell>
    </PlayerProvider>
  );
}
