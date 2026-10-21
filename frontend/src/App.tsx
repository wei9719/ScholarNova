import { Routes, Route } from 'react-router-dom'
import { Toaster } from 'react-hot-toast'
import Layout from './components/Layout/Layout'
import Home from './pages/Home'
import Search from './pages/Search'
import Settings from './pages/Settings'
import Knowledge from './pages/Knowledge'
import KnowledgeAnalysis from './pages/KnowledgeAnalysis'
import RouteDetail from './pages/RouteDetail'
import ResearchAssistant from './pages/ResearchAssistant'
import AssistantStorageGuard from './components/AssistantStorageGuard'

function App() {
  return (
    <>
      <Toaster
        position="top-right"
        toastOptions={{
          className: 'dark:bg-gray-800 dark:text-gray-100',
          duration: 3000,
        }}
      />
      <AssistantStorageGuard />
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Home />} />
          <Route path="/search" element={<Search />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/knowledge" element={<Knowledge />} />
          <Route path="/knowledge/analysis" element={<KnowledgeAnalysis />} />
          <Route path="/knowledge/route/:id" element={<RouteDetail />} />
          <Route path="/assistant" element={<ResearchAssistant />} />
        </Route>
      </Routes>
    </>
  )
}

export default App
