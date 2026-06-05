import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import PromptLab from './pages/PromptLab';
import './styles.css';

// Скрытая страница тест-промтов: доступна ТОЛЬКО по прямому URL c #promptlab
// (например .../test_news/#promptlab). Ссылок на неё в интерфейсе нет.
const isPromptLab = typeof window !== 'undefined' && window.location.hash.toLowerCase().includes('promptlab');

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {isPromptLab ? <PromptLab /> : <App />}
  </React.StrictMode>,
);
