'use client';

import { useEffect } from 'react';
import { useParams, useSearchParams } from 'next/navigation';
import { useApp } from '@/lib/AppContext';
import { api } from '@/lib/api';
import { Loader2 } from 'lucide-react';

/**
 * Redirect page: sets the active experiment in AppContext and navigates to /.
 * Supports deep links like /experiments/{id}?session={sid}.
 */
export default function ExperimentRedirect() {
  const params = useParams();
  const searchParams = useSearchParams();
  const experimentId = params.id as string;
  const sessionId = searchParams.get('session') || null;
  const { setActiveExperiment } = useApp();

  useEffect(() => {
    const redirect = async () => {
      try {
        if (sessionId) {
          setActiveExperiment(experimentId, sessionId);
        } else {
          // Fetch experiment to get latest session
          const exp = await api.getExperiment(experimentId);
          const sid = exp.sessions?.[0]?.id || null;
          setActiveExperiment(experimentId, sid);
        }
      } catch {
        // If experiment not found, just go home
      }
      window.location.href = '/';
    };
    redirect();
  }, [experimentId, sessionId, setActiveExperiment]);

  return (
    <div className="min-h-screen bg-black flex items-center justify-center">
      <Loader2 className="w-6 h-6 text-gray-500 animate-spin" />
    </div>
  );
}
