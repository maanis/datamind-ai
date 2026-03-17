import { useMutation } from '@tanstack/react-query';

interface Source {
  id: string;
  text: string;
  score: number;
  source: string;
}

interface RobotResponse {
  answer: string;
  sources: Source[];
}

export const callRobotAPI = async (question: string) => {
  const userStr = localStorage.getItem('user');
  if (!userStr) {
    throw new Error('User not found in localStorage');
  }
  const user = JSON.parse(userStr);
  const apiKey = user.api_key;
  if (!apiKey) {
    throw new Error('API key not found in user data');
  }

  const response = await fetch(`http://localhost:3000/api/robot/get-answer/${apiKey}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ question }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    const errorMessage = errorData.message || `HTTP ${response.status}`;
    const errorDetail = errorData.error?.detail || response.statusText;
    throw new Error(`${errorMessage}: ${errorDetail}`);
  }

  return response.json() as Promise<RobotResponse>;
};