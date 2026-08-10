import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import AddRepo from './pages/AddRepo'
import RepoDetail from './pages/RepoDetail'
import StudyGuideView from './pages/StudyGuideView'
import QuizTaker from './pages/QuizTaker'
import AttemptResults from './pages/AttemptResults'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/repos/new" element={<AddRepo />} />
        <Route path="/repos/:repoId" element={<RepoDetail />} />
        <Route path="/study-guides/:studyGuideId" element={<StudyGuideView />} />
        <Route path="/quizzes/:quizId" element={<QuizTaker />} />
        <Route path="/attempts/:attemptId" element={<AttemptResults />} />
      </Routes>
    </BrowserRouter>
  )
}

export default App
