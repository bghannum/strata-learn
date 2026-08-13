import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import AddRepo from './pages/AddRepo'
import RepoDetail from './pages/RepoDetail'
import StudyGuideView from './pages/StudyGuideView'
import QuizTaker from './pages/QuizTaker'
import AttemptResults from './pages/AttemptResults'
import Login from './pages/Login'
import Register from './pages/Register'
import AppLayout from './components/AppLayout'
import { AuthProvider } from './auth/AuthContext'

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />
          <Route element={<AppLayout />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/repos/new" element={<AddRepo />} />
            <Route path="/repos/:repoId" element={<RepoDetail />} />
            <Route path="/study-guides/:studyGuideId" element={<StudyGuideView />} />
            <Route path="/quizzes/:quizId" element={<QuizTaker />} />
            <Route path="/attempts/:attemptId" element={<AttemptResults />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}

export default App
