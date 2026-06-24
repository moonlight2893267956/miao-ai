"use client";

import { useEffect, useState } from "react";
import { listAgents, type Agent } from "@/lib/api";

export function HeaderStats() {
  const [agents, setAgents] = useState<Agent[] | null>(null);

  useEffect(() => {
    listAgents()
      .then(setAgents)
      .catch(() => setAgents(null));
  }, []);

  const runningCount = agents?.filter((agent) => agent.status === "running").length ?? 0;

  return (
    <div className="header-right">
      <div className="header-stat">
        <span className="dot" />
        <span>Backend connected</span>
      </div>
      {agents && (
        <div className="header-stat header-stat-success">
          <span>
            {agents.length} agents · {runningCount} running
          </span>
        </div>
      )}
    </div>
  );
}
