import { useEffect, useMemo, useState, type FormEvent } from "react";
import "./App.css";

type PrinterState = "setup" | "ready" | "printing" | "attention" | "offline";
type JobState =
  | "draft"
  | "queued"
  | "assigned"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";
type View = "overview" | "queue" | "materials" | "files" | "printer";

type Printer = {
  id: string;
  name: string;
  manufacturer: string;
  model: string;
  adapter_type: string;
  host: string;
  location: string;
  state: PrinterState;
  percent_complete: number | null;
  active_job: string | null;
  last_seen_at: string | null;
  material: string;
  material_color: string;
  bed_temp: number | null;
  nozzle_temp: number | null;
  run_hours: number;
  maintenance_due_hours: number;
  favorite: boolean;
  note: string;
  connection_state: "unconfigured" | "connected" | "error" | "manual";
  connection_message: string | null;
  last_refreshed_at: string | null;
  capabilities: string[];
};
type Job = {
  id: string;
  name: string;
  filename: string;
  target_printer_id: string | null;
  material: string;
  estimated_minutes: number;
  file_id: string | null;
  state: JobState;
  created_at: string;
  queue_position: number | null;
  delivery_state: "not_ready" | "ready" | "uploaded" | "error";
  remote_file_path: string | null;
  delivered_at: string | null;
};
type Material = {
  id: string;
  name: string;
  material_type: string;
  brand: string;
  color: string;
  remaining_percent: number;
  spool_grams: number;
  assigned_printer_id: string | null;
  created_at: string;
  updated_at: string;
};
type FileItem = {
  id: string;
  name: string;
  filename: string;
  file_type: string;
  material: string;
  estimated_minutes: number | null;
  notes: string;
  created_at: string;
  stored_filename: string | null;
  size_bytes: number | null;
  sha256: string | null;
};
type Activity = {
  id: string;
  occurred_at: string;
  level: "info" | "success" | "warning";
  message: string;
};
type HubData = {
  printers: Printer[];
  jobs: Job[];
  materials: Material[];
  files: FileItem[];
  activity: Activity[];
};

const now = new Date().toISOString();
const preview: HubData = {
  printers: [
    {
      id: "preview-bambu",
      name: "Forge North",
      manufacturer: "Bambu Lab",
      model: "P1S",
      adapter_type: "bambu_lan",
      host: "192.168.1.88",
      location: "Shop floor",
      state: "printing",
      percent_complete: 62,
      active_job: "Pallet clips - batch 04",
      last_seen_at: now,
      material: "PETG - Black",
      material_color: "#20252a",
      bed_temp: 80,
      nozzle_temp: 255,
      run_hours: 148,
      maintenance_due_hours: 250,
      favorite: true,
      note: "Clean build plate every week.",
      connection_state: "unconfigured",
      connection_message: "Bambu LAN setup is coming next.",
      last_refreshed_at: null,
      capabilities: [],
    },
    {
      id: "preview-prusa",
      name: "Forge West",
      manufacturer: "Prusa",
      model: "MK4",
      adapter_type: "prusalink",
      host: "192.168.1.91",
      location: "Shop floor",
      state: "ready",
      percent_complete: null,
      active_job: null,
      last_seen_at: now,
      material: "PETG - Orange",
      material_color: "#e8913a",
      bed_temp: null,
      nozzle_temp: null,
      run_hours: 91,
      maintenance_due_hours: 250,
      favorite: false,
      note: "",
      connection_state: "unconfigured",
      connection_message: null,
      last_refreshed_at: null,
      capabilities: [],
    },
  ],
  jobs: [
    {
      id: "preview-job-1",
      name: "Pallet clips - batch 04",
      filename: "pallet-clips-b04.3mf",
      target_printer_id: "preview-bambu",
      material: "PETG - Black",
      estimated_minutes: 184,
      file_id: "preview-file-1",
      state: "running",
      created_at: now,
      queue_position: null,
      delivery_state: "uploaded",
      remote_file_path: "pallet-clips-b04.3mf",
      delivered_at: now,
    },
    {
      id: "preview-job-2",
      name: "Fixture arm - left",
      filename: "fixture-arm-left.gcode",
      target_printer_id: "preview-prusa",
      material: "PETG - Orange",
      estimated_minutes: 92,
      file_id: null,
      state: "queued",
      created_at: now,
      queue_position: 1,
      delivery_state: "not_ready",
      remote_file_path: null,
      delivered_at: null,
    },
    {
      id: "preview-job-3",
      name: "Cable guide set",
      filename: "cable-guides-v3.3mf",
      target_printer_id: null,
      material: "PLA - White",
      estimated_minutes: 48,
      file_id: null,
      state: "queued",
      created_at: now,
      queue_position: 2,
      delivery_state: "not_ready",
      remote_file_path: null,
      delivered_at: null,
    },
  ],
  materials: [
    {
      id: "preview-material-1",
      name: "Black PETG",
      material_type: "PETG",
      brand: "Polymaker",
      color: "#20252a",
      remaining_percent: 43,
      spool_grams: 1000,
      assigned_printer_id: "preview-bambu",
      created_at: now,
      updated_at: now,
    },
    {
      id: "preview-material-2",
      name: "White PLA",
      material_type: "PLA",
      brand: "Overture",
      color: "#e5e4dd",
      remaining_percent: 18,
      spool_grams: 1000,
      assigned_printer_id: null,
      created_at: now,
      updated_at: now,
    },
  ],
  files: [
    {
      id: "preview-file-1",
      name: "Pallet clips - batch 04",
      filename: "pallet-clips-b04.3mf",
      file_type: "3mf",
      material: "PETG - Black",
      estimated_minutes: 184,
      notes: "Validated for the P1S profile.",
      created_at: now,
      stored_filename: "preview.3mf",
      size_bytes: 1640000,
      sha256: "preview",
    },
  ],
  activity: [
    {
      id: "preview-1",
      occurred_at: now,
      level: "success",
      message: "Forge North confirmed its active job.",
    },
    {
      id: "preview-2",
      occurred_at: now,
      level: "info",
      message: "Fixture arm - left entered the queue.",
    },
    {
      id: "preview-3",
      occurred_at: now,
      level: "warning",
      message: "White PLA is below the restock threshold.",
    },
  ],
};

const labels: Record<PrinterState | JobState, string> = {
  setup: "Setup needed",
  ready: "Ready",
  printing: "Printing",
  attention: "Needs review",
  offline: "Offline",
  draft: "Draft",
  queued: "Queued",
  assigned: "Assigned",
  running: "Running",
  completed: "Complete",
  failed: "Failed",
  cancelled: "Cancelled",
};

function relativeTime(timestamp: string | null) {
  if (!timestamp) return "not seen yet";
  const minutes = Math.max(
    0,
    Math.round((Date.now() - new Date(timestamp).getTime()) / 60000),
  );
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 1440) return `${Math.round(minutes / 60)}h ago`;
  return `${Math.round(minutes / 1440)}d ago`;
}
function duration(minutes: number | null) {
  if (!minutes) return "-";
  const hours = Math.floor(minutes / 60);
  return hours ? `${hours}h ${minutes % 60}m` : `${minutes}m`;
}
function fileSize(bytes: number | null) {
  if (bytes === null) return "Catalog record";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
function statusClass(state: PrinterState | JobState) {
  return `status-pill ${state}`;
}

async function api(path: string, options?: RequestInit) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(
      body?.detail || "The local service could not complete that action.",
    );
  }
  return response.status === 204 ? null : response.json();
}

async function uploadFile(form: FormData) {
  const response = await fetch("/api/files/upload", {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(
      body?.detail || "The file could not be uploaded to the local library.",
    );
  }
  return response.json();
}

function App() {
  const [data, setData] = useState<HubData>(preview);
  const [serviceMode, setServiceMode] = useState<"preview" | "local">(
    "preview",
  );
  const [view, setView] = useState<View>("overview");
  const [selectedPrinterId, setSelectedPrinterId] = useState<string | null>(
    null,
  );
  const [modal, setModal] = useState<
    "printer" | "job" | "material" | "file" | null
  >(null);
  const [connectionPrinterId, setConnectionPrinterId] = useState<string | null>(
    null,
  );
  const [notice, setNotice] = useState<string | null>(null);
  const [jobSeed, setJobSeed] = useState<Partial<Job> | null>(null);

  const load = async () => {
    try {
      const payload = (await api("/api/dashboard")) as Partial<HubData>;
      setData({
        printers: Array.isArray(payload.printers) ? payload.printers : [],
        jobs: Array.isArray(payload.jobs) ? payload.jobs : [],
        materials: Array.isArray(payload.materials) ? payload.materials : [],
        files: Array.isArray(payload.files) ? payload.files : [],
        activity: Array.isArray(payload.activity) ? payload.activity : [],
      });
      setServiceMode("local");
    } catch {
      setServiceMode("preview");
    }
  };
  useEffect(() => {
    void load();
    const refresh = window.setInterval(() => void load(), 15_000);
    return () => window.clearInterval(refresh);
  }, []);

  const summary = useMemo(
    () => ({
      printing: data.printers.filter((item) => item.state === "printing")
        .length,
      ready: data.printers.filter((item) => item.state === "ready").length,
      attention: data.printers.filter((item) =>
        ["attention", "setup", "offline"].includes(item.state),
      ).length,
      queued: data.jobs.filter((item) => item.state === "queued").length,
    }),
    [data],
  );
  const queued = useMemo(
    () =>
      data.jobs
        .filter((item) => item.state === "queued")
        .sort(
          (a, b) => (a.queue_position ?? 99999) - (b.queue_position ?? 99999),
        ),
    [data.jobs],
  );
  const selected =
    data.printers.find((item) => item.id === selectedPrinterId) ?? null;
  const lowMaterials = data.materials.filter(
    (item) => item.remaining_percent < 20,
  );

  const changeView = (next: Exclude<View, "printer">) => {
    setView(next);
    setSelectedPrinterId(null);
  };
  const selectPrinter = (printerId: string) => {
    setSelectedPrinterId(printerId);
    setView("printer");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const mutate = async (work: () => Promise<unknown>, success: string) => {
    try {
      await work();
      setNotice(success);
      setModal(null);
      setJobSeed(null);
      await load();
    } catch (error) {
      setNotice(
        error instanceof Error
          ? error.message
          : "Action could not be completed.",
      );
    }
  };
  const remove = async (path: string, label: string) => {
    if (
      !window.confirm(
        `Remove ${label}? This only affects this local workspace.`,
      )
    )
      return;
    await mutate(
      () => api(path, { method: "DELETE" }),
      `${label} was removed.`,
    );
  };
  const moveQueue = async (job: Job, direction: -1 | 1) => {
    const index = queued.findIndex((item) => item.id === job.id);
    const target = queued[index + direction];
    if (!target) return;
    await mutate(async () => {
      await api(`/api/jobs/${target.id}`, {
        method: "PATCH",
        body: JSON.stringify({ queue_position: job.queue_position }),
      });
      return api(`/api/jobs/${job.id}`, {
        method: "PATCH",
        body: JSON.stringify({ queue_position: target.queue_position }),
      });
    }, "Queue order was updated.");
  };
  const openJobForFile = (file: FileItem) => {
    setJobSeed({
      name: file.name,
      filename: file.filename,
      file_id: file.id,
      material: file.material,
      estimated_minutes: file.estimated_minutes ?? 60,
    });
    setModal("job");
  };

  return (
    <div className="app-shell">
      <header className="app-header">
        <button
          className="wordmark"
          onClick={() => changeView("overview")}
          aria-label="Open Mirdain Print Hub"
        >
          <span className="wordmark-mark" aria-hidden="true">
            MP
          </span>
          <span>
            MIRDAIN <strong>PRINT HUB</strong>
          </span>
        </button>
        <nav className="main-nav" aria-label="Primary navigation">
          <button
            className={view === "overview" ? "active" : ""}
            onClick={() => changeView("overview")}
          >
            Fleet
          </button>
          <button
            className={view === "queue" ? "active" : ""}
            onClick={() => changeView("queue")}
          >
            Queue <span>{summary.queued}</span>
          </button>
          <button
            className={view === "files" ? "active" : ""}
            onClick={() => changeView("files")}
          >
            Files <span>{data.files.length}</span>
          </button>
          <button
            className={view === "materials" ? "active" : ""}
            onClick={() => changeView("materials")}
          >
            Materials <span>{data.materials.length}</span>
          </button>
        </nav>
        <div className="header-actions">
          <span className={`service-chip ${serviceMode}`}>
            <i />
            {serviceMode === "local" ? "Local workspace" : "Preview mode"}
          </span>
          <button className="button quiet" onClick={() => setModal("printer")}>
            Add printer
          </button>
          <button className="button primary" onClick={() => setModal("job")}>
            Queue job
          </button>
        </div>
      </header>

      {serviceMode === "preview" && (
        <div className="workspace-banner">
          <strong>Preview data is shown.</strong>
          <span>
            Start the local service to save your fleet, jobs, materials, and
            files on this computer.
          </span>
        </div>
      )}
      {notice && (
        <button
          className="notice floating-notice"
          onClick={() => setNotice(null)}
        >
          {notice}
          <span>Dismiss</span>
        </button>
      )}

      {view === "overview" && (
        <Overview
          data={data}
          summary={summary}
          lowMaterials={lowMaterials}
          onView={changeView}
          onPrinter={selectPrinter}
          onAddPrinter={() => setModal("printer")}
          onAddJob={() => setModal("job")}
        />
      )}
      {view === "queue" && (
        <QueueView
          jobs={data.jobs}
          printers={data.printers}
          onAdd={() => setModal("job")}
          onMove={moveQueue}
          onDeliver={(job) =>
            mutate(
              () => api(`/api/jobs/${job.id}/deliver`, { method: "POST" }),
              `${job.name} was uploaded to its assigned printer.`,
            )
          }
          onControl={(job, action) =>
            mutate(
              () =>
                api(`/api/jobs/${job.id}/control`, {
                  method: "POST",
                  body: JSON.stringify({
                    action,
                    confirmation: action.toUpperCase(),
                  }),
                }),
              `${job.name} received the confirmed ${action} command.`,
            )
          }
          onRemove={(job) => remove(`/api/jobs/${job.id}`, job.name)}
        />
      )}
      {view === "materials" && (
        <MaterialsView
          materials={data.materials}
          printers={data.printers}
          onAdd={() => setModal("material")}
          onRemove={(material) =>
            remove(`/api/materials/${material.id}`, material.name)
          }
        />
      )}
      {view === "files" && (
        <FilesView
          files={data.files}
          onAdd={() => setModal("file")}
          onQueue={openJobForFile}
          onRemove={(file) => remove(`/api/files/${file.id}`, file.name)}
        />
      )}
      {view === "printer" && selected && (
        <PrinterView
          printer={selected}
          jobs={data.jobs}
          onBack={() => changeView("overview")}
          onUpdate={(payload, message) =>
            mutate(
              () =>
                api(`/api/printers/${selected.id}`, {
                  method: "PATCH",
                  body: JSON.stringify(payload),
                }),
              message,
            )
          }
          onRemove={() => remove(`/api/printers/${selected.id}`, selected.name)}
          onAddJob={() => {
            setJobSeed({
              target_printer_id: selected.id,
              material: selected.material,
            });
            setModal("job");
          }}
          onConnect={() => setConnectionPrinterId(selected.id)}
          onRefresh={() =>
            mutate(
              () =>
                api(`/api/printers/${selected.id}/refresh`, { method: "POST" }),
              `${selected.name} telemetry was refreshed.`,
            )
          }
        />
      )}

      {modal === "printer" && (
        <PrinterModal
          onClose={() => setModal(null)}
          onSubmit={(payload) =>
            mutate(
              () =>
                api("/api/printers", {
                  method: "POST",
                  body: JSON.stringify(payload),
                }),
              `${payload.name} was added to the local fleet.`,
            )
          }
        />
      )}
      {modal === "job" && (
        <JobModal
          printers={data.printers}
          seed={jobSeed}
          onClose={() => {
            setModal(null);
            setJobSeed(null);
          }}
          onSubmit={(payload) =>
            mutate(
              () =>
                api("/api/jobs", {
                  method: "POST",
                  body: JSON.stringify(payload),
                }),
              `${payload.name} was added to the queue.`,
            )
          }
        />
      )}
      {modal === "material" && (
        <MaterialModal
          printers={data.printers}
          onClose={() => setModal(null)}
          onSubmit={(payload) =>
            mutate(
              () =>
                api("/api/materials", {
                  method: "POST",
                  body: JSON.stringify(payload),
                }),
              `${payload.name} was added to materials.`,
            )
          }
        />
      )}
      {modal === "file" && (
        <FileModal
          onClose={() => setModal(null)}
          onSubmit={(form) =>
            mutate(
              () => uploadFile(form),
              `${String(form.get("name"))} was uploaded to the local file library.`,
            )
          }
        />
      )}
      {connectionPrinterId &&
        data.printers.find((printer) => printer.id === connectionPrinterId) && (
          <ConnectionModal
            printer={
              data.printers.find(
                (printer) => printer.id === connectionPrinterId,
              )!
            }
            onClose={() => setConnectionPrinterId(null)}
            onSubmit={(payload) =>
              mutate(
                () =>
                  api(`/api/printers/${connectionPrinterId}/connection`, {
                    method: "POST",
                    body: JSON.stringify(payload),
                  }),
                "Local printer connection verified. Monitoring is now enabled.",
              ).finally(() => setConnectionPrinterId(null))
            }
          />
        )}
    </div>
  );
}

function Overview({
  data,
  summary,
  lowMaterials,
  onView,
  onPrinter,
  onAddPrinter,
  onAddJob,
}: {
  data: HubData;
  summary: Record<string, number>;
  lowMaterials: Material[];
  onView: (view: Exclude<View, "printer">) => void;
  onPrinter: (id: string) => void;
  onAddPrinter: () => void;
  onAddJob: () => void;
}) {
  return (
    <main className="home-main">
      <section className="overview-heading">
        <div>
          <p className="eyebrow">LOCAL PRINT OPERATIONS</p>
          <h1>Run your print floor from one private workspace.</h1>
          <p className="lede">
            Fleet health, a shared production queue, materials, service
            reminders, and a file library - all stored locally on your computer.
          </p>
        </div>
        <div className="overview-state">
          <span>Fleet health</span>
          <strong>
            {summary.attention
              ? `${summary.attention} need attention`
              : "All clear"}
          </strong>
          <small>{data.printers.length} printers in this workspace</small>
        </div>
      </section>
      <section className="metric-row" aria-label="Fleet summary">
        <Metric
          label="Printing now"
          value={summary.printing}
          detail="active production jobs"
          tone="amber"
        />
        <Metric
          label="Ready"
          value={summary.ready}
          detail="available for verified jobs"
        />
        <Metric
          label="Needs attention"
          value={summary.attention}
          detail="offline or needs setup"
          tone={summary.attention ? "warning" : undefined}
        />
        <Metric
          label="Queue depth"
          value={summary.queued}
          detail="jobs waiting to run"
        />
      </section>
      <section className="printer-view">
        <div className="section-heading">
          <div>
            <p className="eyebrow">FLEET STATUS</p>
            <h2>
              Printers <span>{data.printers.length}</span>
            </h2>
          </div>
          <button className="text-button" onClick={onAddPrinter}>
            Add a printer <span>-&gt;</span>
          </button>
        </div>
        <div className="printer-table-wrap">
          {data.printers.length ? (
            <table className="printer-table">
              <thead>
                <tr>
                  <th>Printer</th>
                  <th>Active job</th>
                  <th>Status</th>
                  <th>Material</th>
                  <th>Service</th>
                  <th aria-label="Open printer" />
                </tr>
              </thead>
              <tbody>
                {data.printers.map((printer) => (
                  <tr key={printer.id} onClick={() => onPrinter(printer.id)}>
                    <td>
                      <div className="printer-cell">
                        <i className={`state-dot ${printer.state}`} />
                        <div>
                          <strong>{printer.name}</strong>
                          <span>
                            {printer.manufacturer} / {printer.model}
                          </span>
                        </div>
                      </div>
                    </td>
                    <td>
                      <div className="job-cell">
                        <strong>{printer.active_job || "No active job"}</strong>
                        <span>
                          {printer.location ||
                            printer.host ||
                            "No location set"}
                        </span>
                      </div>
                    </td>
                    <td>
                      <span className={statusClass(printer.state)}>
                        {labels[printer.state]}
                      </span>
                    </td>
                    <td>
                      {printer.material || (
                        <span className="telemetry">Not loaded</span>
                      )}
                    </td>
                    <td>
                      {Math.max(
                        0,
                        printer.maintenance_due_hours - printer.run_hours,
                      )}
                      h left
                    </td>
                    <td>
                      <button
                        className="row-open"
                        aria-label={`Open ${printer.name}`}
                        onClick={(event) => {
                          event.stopPropagation();
                          onPrinter(printer.id);
                        }}
                      >
                        -&gt;
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <EmptyState
              title="Bring your first printer online."
              copy="Add a printer to start a local fleet record."
              action="Add printer"
              onAction={onAddPrinter}
            />
          )}
        </div>
      </section>
      <section className="operations-grid">
        <section className="surface-panel queue-panel">
          <div className="section-heading compact">
            <div>
              <p className="eyebrow">PRODUCTION QUEUE</p>
              <h2>Next to run</h2>
            </div>
            <button className="text-button" onClick={() => onView("queue")}>
              Open queue <span>-&gt;</span>
            </button>
          </div>
          <QueuePreview data={data} onAdd={onAddJob} />
        </section>
        <section className="surface-panel activity-panel">
          <div className="section-heading compact">
            <div>
              <p className="eyebrow">WATCH LIST</p>
              <h2>Materials</h2>
            </div>
            <button className="text-button" onClick={() => onView("materials")}>
              Open shelf
            </button>
          </div>
          <div className="activity-list">
            {lowMaterials.length ? (
              lowMaterials.slice(0, 3).map((material) => (
                <article className="activity-item" key={material.id}>
                  <i className="warning" />
                  <div>
                    <p>
                      <strong>{material.name}</strong> is at{" "}
                      {material.remaining_percent}%.
                    </p>
                    <time>{material.spool_grams}g spool · restock soon</time>
                  </div>
                </article>
              ))
            ) : (
              <EmptyState
                title="No material alerts."
                copy="Your registered spools are above the restock threshold."
              />
            )}
          </div>
        </section>
      </section>
      <section className="surface-panel recent-panel">
        <div className="section-heading compact">
          <div>
            <p className="eyebrow">OPERATIONS LOG</p>
            <h2>What changed</h2>
          </div>
        </div>
        <div className="activity-list">
          {data.activity.slice(0, 5).map((item) => (
            <article className="activity-item" key={item.id}>
              <i className={item.level} />
              <div>
                <p>{item.message}</p>
                <time>{relativeTime(item.occurred_at)}</time>
              </div>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}

function QueuePreview({ data, onAdd }: { data: HubData; onAdd: () => void }) {
  const jobs = data.jobs
    .filter((job) => job.state === "queued")
    .sort((a, b) => (a.queue_position ?? 999) - (b.queue_position ?? 999))
    .slice(0, 4);
  return (
    <div className="queue-table-wrap">
      {jobs.length ? (
        <table className="queue-table">
          <thead>
            <tr>
              <th>Job</th>
              <th>Printer</th>
              <th>Material</th>
              <th>Estimate</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id}>
                <td>
                  <strong>{job.name}</strong>
                  <span>{job.filename}</span>
                </td>
                <td>
                  {data.printers.find(
                    (printer) => printer.id === job.target_printer_id,
                  )?.name || "Unassigned"}
                </td>
                <td>{job.material || "Not set"}</td>
                <td>{duration(job.estimated_minutes)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <EmptyState
          title="No jobs waiting."
          copy="Queue a compatible file when you are ready."
          action="Queue job"
          onAction={onAdd}
        />
      )}
    </div>
  );
}

function QueueView({
  jobs,
  printers,
  onAdd,
  onMove,
  onDeliver,
  onControl,
  onRemove,
}: {
  jobs: Job[];
  printers: Printer[];
  onAdd: () => void;
  onMove: (job: Job, direction: -1 | 1) => void;
  onDeliver: (job: Job) => void;
  onControl: (
    job: Job,
    action: "start" | "pause" | "resume" | "cancel",
  ) => void;
  onRemove: (job: Job) => void;
}) {
  const queue = jobs
    .filter((job) => job.state === "queued")
    .sort((a, b) => (a.queue_position ?? 99999) - (b.queue_position ?? 99999));
  const activeAndRecent = jobs
    .filter((job) => job.state !== "queued")
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
  const confirmControl = (
    job: Job,
    action: "start" | "pause" | "resume" | "cancel",
  ) => {
    if (
      window.confirm(
        `Send ${action.toUpperCase()} to ${job.name}'s assigned printer? This sends a real local-network command.`,
      )
    )
      onControl(job, action);
  };
  return (
    <main className="home-main view-main">
      <section className="view-heading">
        <div>
          <p className="eyebrow">PRODUCTION QUEUE</p>
          <h1>Plan the next print before a machine goes idle.</h1>
          <p className="lede">
            Upload a stored file first. Every physical printer command asks for
            final confirmation.
          </p>
        </div>
        <button className="button primary" onClick={onAdd}>
          Queue job
        </button>
      </section>
      <section className="surface-panel queue-full">
        {queue.length ? (
          <table className="queue-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Job</th>
                <th>Printer</th>
                <th>Material</th>
                <th>Estimate</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {queue.map((job, index) => (
                <tr key={job.id}>
                  <td>
                    <div className="queue-order">
                      <b>{job.queue_position}</b>
                      <span>
                        <button
                          disabled={!index}
                          onClick={() => onMove(job, -1)}
                        >
                          Up
                        </button>
                        <button
                          disabled={index === queue.length - 1}
                          onClick={() => onMove(job, 1)}
                        >
                          Down
                        </button>
                      </span>
                    </div>
                  </td>
                  <td>
                    <strong>{job.name}</strong>
                    <span>
                      {job.filename} · {job.delivery_state === "uploaded"
                        ? "Uploaded"
                        : job.file_id
                          ? "Ready to upload"
                          : "No file attached"}
                    </span>
                  </td>
                  <td>
                    {printers.find(
                      (printer) => printer.id === job.target_printer_id,
                    )?.name || "Unassigned"}
                  </td>
                  <td>{job.material || "Not set"}</td>
                  <td>{duration(job.estimated_minutes)}</td>
                  <td>
                    <div className="table-actions">
                      {job.file_id &&
                        job.target_printer_id &&
                        job.delivery_state !== "uploaded" && (
                          <button
                            className="button quiet"
                            onClick={() => onDeliver(job)}
                          >
                            Upload
                          </button>
                        )}
                      {job.delivery_state === "uploaded" && (
                        <button
                          className="button primary"
                          onClick={() => confirmControl(job, "start")}
                        >
                          Start
                        </button>
                      )}
                      <button
                        className="button quiet"
                        onClick={() => onRemove(job)}
                      >
                        Remove
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <EmptyState
            title="No jobs are waiting."
            copy="Add a compatible file to your production queue."
            action="Queue job"
            onAction={onAdd}
          />
        )}
      </section>
      <section className="surface-panel completed-panel">
        <div className="section-heading compact">
          <div>
            <p className="eyebrow">ACTIVE & RECENT</p>
            <h2>Started, completed, and cancelled jobs</h2>
          </div>
        </div>
        {activeAndRecent.length ? (
          <div className="recent-jobs">
            {activeAndRecent.slice(0, 8).map((job) => (
              <article key={job.id}>
                <div>
                  <strong>{job.name}</strong>
                  <span>
                    {printers.find(
                      (printer) => printer.id === job.target_printer_id,
                    )?.name || "No printer"}{" "}
                    · {duration(job.estimated_minutes)}
                  </span>
                </div>
                <span className={statusClass(job.state)}>
                  {labels[job.state]}
                </span>
                {job.state === "running" && (
                  <button
                    className="button quiet"
                    onClick={() => confirmControl(job, "pause")}
                  >
                    Pause
                  </button>
                )}
                {job.state === "running" && (
                  <button
                    className="button quiet"
                    onClick={() => confirmControl(job, "resume")}
                  >
                    Resume
                  </button>
                )}
                {job.state === "running" && (
                  <button
                    className="button danger-button"
                    onClick={() => confirmControl(job, "cancel")}
                  >
                    Cancel
                  </button>
                )}
              </article>
            ))}
          </div>
        ) : (
          <p className="panel-footer">
            Jobs remain local until you explicitly upload them to a compatible
            connected printer.
          </p>
        )}
      </section>
    </main>
  );
}

function MaterialsView({
  materials,
  printers,
  onAdd,
  onRemove,
}: {
  materials: Material[];
  printers: Printer[];
  onAdd: () => void;
  onRemove: (material: Material) => void;
}) {
  return (
    <main className="home-main view-main">
      <section className="view-heading">
        <div>
          <p className="eyebrow">MATERIAL SHELF</p>
          <h1>Know what is loaded and what needs restocking.</h1>
          <p className="lede">
            Spool records stay in your local workspace. Inventory is never sent
            to a cloud account.
          </p>
        </div>
        <button className="button primary" onClick={onAdd}>
          Add spool
        </button>
      </section>
      <section className="material-grid">
        {materials.length ? (
          materials.map((material) => (
            <article className="material-card" key={material.id}>
              <div className="material-card-head">
                <span
                  className="spool-swatch"
                  style={{ borderColor: material.color }}
                >
                  <i style={{ background: material.color }} />
                </span>
                <b>{material.remaining_percent}%</b>
              </div>
              <p className="eyebrow">{material.material_type}</p>
              <h2>{material.name}</h2>
              <p>
                {material.brand || "Unbranded"} · {material.spool_grams}g spool
              </p>
              <div className="material-bar">
                <i
                  style={{
                    width: `${material.remaining_percent}%`,
                    background: material.color,
                  }}
                />
              </div>
              <small>
                {printers.find(
                  (printer) => printer.id === material.assigned_printer_id,
                )?.name || "On shelf"}
              </small>
              <button
                className="text-button remove-link"
                onClick={() => onRemove(material)}
              >
                Remove
              </button>
            </article>
          ))
        ) : (
          <EmptyState
            title="Your material shelf is empty."
            copy="Add your first spool to track stock before a job reaches the bed."
            action="Add spool"
            onAction={onAdd}
          />
        )}
      </section>
    </main>
  );
}

function FilesView({
  files,
  onAdd,
  onQueue,
  onRemove,
}: {
  files: FileItem[];
  onAdd: () => void;
  onQueue: (file: FileItem) => void;
  onRemove: (file: FileItem) => void;
}) {
  return (
    <main className="home-main view-main">
      <section className="view-heading">
        <div>
          <p className="eyebrow">FILE LIBRARY</p>
          <h1>Store print-ready files once, then queue them anywhere.</h1>
          <p className="lede">
            Files stay on this computer until you explicitly upload one to a
            compatible local printer.
          </p>
        </div>
        <button className="button primary" onClick={onAdd}>
          Upload file
        </button>
      </section>
      <section className="file-grid">
        {files.length ? (
          files.map((file) => (
            <article className="file-card" key={file.id}>
              <span className="file-type">{file.file_type.toUpperCase()}</span>
              <h2>{file.name}</h2>
              <p>{file.filename}</p>
              <dl>
                <div>
                  <dt>Material</dt>
                  <dd>{file.material || "Not set"}</dd>
                </div>
                <div>
                  <dt>Estimate</dt>
                  <dd>{duration(file.estimated_minutes)}</dd>
                </div>
                <div>
                  <dt>Local copy</dt>
                  <dd>{fileSize(file.size_bytes)}</dd>
                </div>
              </dl>
              {file.notes && <small>{file.notes}</small>}
              <div className="file-actions">
                <button
                  className="button primary"
                  onClick={() => onQueue(file)}
                >
                  Queue file
                </button>
                <button className="button quiet" onClick={() => onRemove(file)}>
                  Remove
                </button>
              </div>
            </article>
          ))
        ) : (
          <EmptyState
            title="Your file library is empty."
            copy="Upload a print-ready file to reuse it across compatible printers."
            action="Upload file"
            onAction={onAdd}
          />
        )}
      </section>
    </main>
  );
}

function PrinterView({
  printer,
  jobs,
  onBack,
  onUpdate,
  onRemove,
  onAddJob,
  onConnect,
  onRefresh,
}: {
  printer: Printer;
  jobs: Job[];
  onBack: () => void;
  onUpdate: (payload: Partial<Printer>, message: string) => void;
  onRemove: () => void;
  onAddJob: () => void;
  onConnect: () => void;
  onRefresh: () => void;
}) {
  const printerQueue = jobs
    .filter(
      (job) => job.target_printer_id === printer.id && job.state === "queued",
    )
    .sort((a, b) => (a.queue_position ?? 999) - (b.queue_position ?? 999));
  const servicePercent = Math.min(
    100,
    Math.round((printer.run_hours / printer.maintenance_due_hours) * 100),
  );
  return (
    <main className="printer-page">
      <button className="back-link" onClick={onBack}>
        &lt;- Back to fleet
      </button>
      <section className="printer-page-title">
        <div>
          <p className="eyebrow">LOCAL PRINTER</p>
          <h1>{printer.name}</h1>
          <p>
            {printer.manufacturer} / {printer.model} <span>·</span>{" "}
            {printer.location || printer.host || "No location set"}
          </p>
        </div>
        <div className="title-status">
          <span className={statusClass(printer.state)}>
            {labels[printer.state]}
          </span>
          <span className="last-seen">
            Seen {relativeTime(printer.last_seen_at)}
          </span>
        </div>
      </section>
      <section className="printer-metrics">
        <Metric
          label="Active job"
          value={printer.active_job ? 1 : 0}
          detail={printer.active_job || "No active job"}
          tone={printer.state === "printing" ? "amber" : undefined}
        />
        <Metric
          label="Completion"
          value={printer.percent_complete || 0}
          detail={
            printer.percent_complete !== null
              ? "manual or adapter reported"
              : "waiting for telemetry"
          }
        />
        <Metric
          label="Queued here"
          value={printerQueue.length}
          detail={
            printerQueue.length ? "ready after current job" : "no job assigned"
          }
        />
        <Metric
          label="Service remaining"
          value={Math.max(0, printer.maintenance_due_hours - printer.run_hours)}
          detail={`of ${printer.maintenance_due_hours}h interval`}
        />
      </section>
      <section className="printer-page-main">
        <section className="camera-panel">
          <div className="camera-heading">
            <div>
              <p className="eyebrow">LIVE VIEW</p>
              <h2>Connection & telemetry</h2>
            </div>
            <span className={`camera-status ${printer.connection_state}`}>
              {printer.connection_state === "connected"
                ? "CONNECTED"
                : "LOCAL ONLY"}
            </span>
          </div>
          <div className="connection-card">
            <strong>
              {printer.connection_state === "connected"
                ? `${printer.adapter_type} monitoring active`
                : printer.connection_state === "error"
                  ? "Connection needs attention"
                  : "Connect this printer"}
            </strong>
            <p>
              {printer.connection_message ||
                "Verify a local connection before enabling telemetry, file delivery, or confirmed controls. Credentials are held in your operating system credential vault, not in this database."}
            </p>
            <dl>
              <div>
                <dt>Local host</dt>
                <dd>{printer.host || "Not configured"}</dd>
              </div>
              <div>
                <dt>Bed / nozzle</dt>
                <dd>
                  {printer.bed_temp ?? "-"}° / {printer.nozzle_temp ?? "-"}°
                </dd>
              </div>
            </dl>
            <div className="connection-actions">
              <button className="button primary" onClick={onConnect}>
                {printer.connection_state === "connected"
                  ? "Update connection"
                  : "Connect printer"}
              </button>
              {printer.connection_state === "connected" && (
                <button className="button quiet" onClick={onRefresh}>
                  Refresh now
                </button>
              )}
            </div>
          </div>
          <section className="printer-edit">
            <div>
              <p className="eyebrow">MANUAL UPDATE</p>
              <h2>Keep the local record current</h2>
            </div>
            <div className="quick-status">
              {(
                [
                  "setup",
                  "ready",
                  "printing",
                  "attention",
                  "offline",
                ] as PrinterState[]
              ).map((state) => (
                <button
                  key={state}
                  className={printer.state === state ? "active" : ""}
                  onClick={() =>
                    onUpdate(
                      {
                        state,
                        percent_complete:
                          state === "printing"
                            ? (printer.percent_complete ?? 0)
                            : null,
                      },
                      `${printer.name} is now ${labels[state].toLowerCase()}.`,
                    )
                  }
                >
                  {labels[state]}
                </button>
              ))}
            </div>
            <div className="edit-fields">
              <label>
                Progress %
                <input
                  type="number"
                  min="0"
                  max="100"
                  value={printer.percent_complete ?? ""}
                  onChange={(event) =>
                    onUpdate(
                      {
                        percent_complete: event.target.value
                          ? Number(event.target.value)
                          : null,
                      },
                      `${printer.name} progress was updated.`,
                    )
                  }
                />
              </label>
              <label>
                Run hours
                <input
                  type="number"
                  min="0"
                  value={printer.run_hours}
                  onChange={(event) =>
                    onUpdate(
                      { run_hours: Number(event.target.value) || 0 },
                      `${printer.name} service hours were updated.`,
                    )
                  }
                />
              </label>
            </div>
          </section>
        </section>
        <aside className="printer-side">
          <section className="side-panel controls-panel">
            <p className="eyebrow">JOB CONTROLS</p>
            <h2>Queue a verified job</h2>
            <p>
              Each job keeps an explicit printer target and must be started
              manually until a hardware adapter is enabled.
            </p>
            <div className="control-actions">
              <button className="button primary" onClick={onAddJob}>
                Add job
              </button>
              <button
                className="button quiet"
                onClick={() =>
                  onUpdate(
                    { favorite: !printer.favorite },
                    `${printer.name} ${printer.favorite ? "was removed from" : "was added to"} favorites.`,
                  )
                }
              >
                {printer.favorite ? "Unfavorite" : "Favorite"}
              </button>
            </div>
          </section>
          <section className="side-panel">
            <div className="side-heading">
              <div>
                <p className="eyebrow">PRINTER QUEUE</p>
                <h2>
                  {printerQueue.length
                    ? `${printerQueue.length} waiting`
                    : "Nothing waiting"}
                </h2>
              </div>
            </div>
            {printerQueue.length ? (
              <div className="mini-queue">
                {printerQueue.map((job) => (
                  <div key={job.id}>
                    <span>{job.queue_position}</span>
                    <p>
                      <strong>{job.name}</strong>
                      <small>
                        {duration(job.estimated_minutes)} /{" "}
                        {job.material || "Material not set"}
                      </small>
                    </p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="quiet-copy">
                Jobs assigned to this printer will wait here.
              </p>
            )}
          </section>
          <section className="side-panel">
            <p className="eyebrow">MAINTENANCE</p>
            <h2>
              {Math.max(0, printer.maintenance_due_hours - printer.run_hours)}h
              until service
            </h2>
            <div className="maintenance-bar">
              <i style={{ width: `${servicePercent}%` }} />
            </div>
            <p className="quiet-copy">
              {printer.note ||
                "Add nozzle, build plate, or setup notes when needed."}
            </p>
          </section>
          <button className="button danger-button" onClick={onRemove}>
            Remove printer
          </button>
        </aside>
      </section>
    </main>
  );
}

function Metric({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: number;
  detail: string;
  tone?: "amber" | "warning";
}) {
  return (
    <article className={`metric ${tone ?? ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}
function EmptyState({
  title,
  copy,
  action,
  onAction,
}: {
  title: string;
  copy: string;
  action?: string;
  onAction?: () => void;
}) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      <span>{copy}</span>
      {action && (
        <button className="button quiet" onClick={onAction}>
          {action}
        </button>
      )}
    </div>
  );
}

function Modal({
  eyebrow,
  title,
  copy,
  children,
  onClose,
}: {
  eyebrow: string;
  title: string;
  copy: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  return (
    <div className="modal-backdrop" role="presentation">
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="modal-heading">
          <div>
            <p className="eyebrow">{eyebrow}</p>
            <h2>{title}</h2>
          </div>
          <button className="close" aria-label="Close" onClick={onClose}>
            x
          </button>
        </div>
        <p className="modal-copy">{copy}</p>
        {children}
      </section>
    </div>
  );
}
function actions(onClose: () => void, label: string) {
  return (
    <div className="modal-actions">
      <button className="button quiet" type="button" onClick={onClose}>
        Cancel
      </button>
      <button className="button primary" type="submit">
        {label}
      </button>
    </div>
  );
}

function PrinterModal({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  onSubmit: (payload: Record<string, unknown>) => void;
}) {
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    onSubmit({
      name: String(form.get("name")),
      manufacturer: String(form.get("manufacturer")),
      model: String(form.get("model")),
      adapter_type: String(form.get("adapter_type")),
      host: String(form.get("host")),
      location: String(form.get("location")),
      material: String(form.get("material")),
      maintenance_due_hours: Number(form.get("maintenance_due_hours")) || 250,
    });
  };
  return (
    <Modal
      eyebrow="LOCAL ONBOARDING"
      title="Add a printer"
      copy="This saves an equipment record locally. Network credentials and hardware commands stay disabled until you choose a verified adapter."
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <label>
          Printer name
          <input name="name" placeholder="e.g. Forge North" required />
        </label>
        <div className="form-split">
          <label>
            Manufacturer
            <input name="manufacturer" placeholder="e.g. Bambu Lab" required />
          </label>
          <label>
            Model
            <input name="model" placeholder="e.g. P1S" required />
          </label>
        </div>
        <div className="form-split">
          <label>
            Connection
            <select name="adapter_type" defaultValue="manual">
              <option value="manual">Manual tracking</option>
              <option value="bambu_lan">Bambu LAN</option>
              <option value="prusalink">PrusaLink</option>
              <option value="octoprint">OctoPrint</option>
              <option value="moonraker">Moonraker</option>
            </select>
          </label>
          <label>
            Local host
            <input name="host" placeholder="192.168.1.88" />
          </label>
        </div>
        <div className="form-split">
          <label>
            Location
            <input name="location" placeholder="e.g. Shop floor" />
          </label>
          <label>
            Service interval (hours)
            <input
              name="maintenance_due_hours"
              type="number"
              min="1"
              defaultValue="250"
            />
          </label>
        </div>
        <label>
          Loaded material
          <input name="material" placeholder="e.g. PETG - Black" />
        </label>
        {actions(onClose, "Save locally")}
      </form>
    </Modal>
  );
}
function ConnectionModal({
  printer,
  onClose,
  onSubmit,
}: {
  printer: Printer;
  onClose: () => void;
  onSubmit: (payload: Record<string, unknown>) => void;
}) {
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    onSubmit({
      adapter_type: String(form.get("adapter_type")),
      host: String(form.get("host")),
      api_key: String(form.get("api_key")) || null,
    });
  };
  return (
    <Modal
      eyebrow="LOCAL CONNECTION"
      title={`Connect ${printer.name}`}
      copy="This verifies local telemetry on your home or shop network. It does not upload a file or issue a printer command. The API key is stored in your operating system credential vault and is never written into the hub database."
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <label>
          Connection type
          <select
            name="adapter_type"
            defaultValue={
              printer.adapter_type === "manual"
                ? "octoprint"
                : printer.adapter_type
            }
          >
            <option value="octoprint">OctoPrint</option>
            <option value="moonraker">Moonraker / Klipper</option>
            <option value="prusalink">PrusaLink</option>
            <option value="bambu_lan">Bambu LAN (coming next)</option>
          </select>
        </label>
        <label>
          Local IP address or URL
          <input
            name="host"
            defaultValue={printer.host}
            placeholder="e.g. 192.168.1.88 or http://printer.local"
            required
          />
        </label>
        <label>
          API key
          <input
            name="api_key"
            type="password"
            placeholder="Leave blank to use the key already saved for this printer"
            autoComplete="off"
          />
        </label>
        <p className="form-help">
          OctoPrint and Moonraker enable file delivery and confirmed controls
          after this status test. PrusaLink remains telemetry-only. No print is
          started, paused, cancelled, or uploaded during this step.
        </p>
        {actions(onClose, "Test and save connection")}
      </form>
    </Modal>
  );
}
function JobModal({
  printers,
  seed,
  onClose,
  onSubmit,
}: {
  printers: Printer[];
  seed: Partial<Job> | null;
  onClose: () => void;
  onSubmit: (payload: Record<string, unknown>) => void;
}) {
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    onSubmit({
      name: String(form.get("name")),
      filename: String(form.get("filename")),
      target_printer_id: String(form.get("target_printer_id")) || null,
      material: String(form.get("material")),
      estimated_minutes: Number(form.get("estimated_minutes")) || 60,
      file_id: seed?.file_id ?? null,
    });
  };
  return (
    <Modal
      eyebrow="PRODUCTION QUEUE"
      title="Queue a job"
      copy="A job stays local until you attach an uploaded file, choose a printer, and explicitly send it."
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <label>
          Job name
          <input
            name="name"
            defaultValue={seed?.name ?? ""}
            placeholder="e.g. Fixture arm - left"
            required
          />
        </label>
        <label>
          File name
          <input
            name="filename"
            defaultValue={seed?.filename ?? ""}
            placeholder="fixture-arm-left.gcode"
            required
          />
        </label>
        <div className="form-split">
          <label>
            Target printer
            <select
              name="target_printer_id"
              defaultValue={seed?.target_printer_id ?? ""}
            >
              <option value="">Assign later</option>
              {printers.map((printer) => (
                <option value={printer.id} key={printer.id}>
                  {printer.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Estimated minutes
            <input
              name="estimated_minutes"
              type="number"
              min="1"
              defaultValue={seed?.estimated_minutes ?? 60}
            />
          </label>
        </div>
        <label>
          Material
          <input
            name="material"
            defaultValue={seed?.material ?? ""}
            placeholder="e.g. PETG - Orange"
          />
        </label>
        {seed?.file_id ? (
          <p className="quiet-copy">
            An uploaded local file is attached to this job.
          </p>
        ) : (
          <p className="quiet-copy">
            Create this job from the File Library to attach an uploaded print
            file.
          </p>
        )}
        {actions(onClose, "Add to queue")}
      </form>
    </Modal>
  );
}
function MaterialModal({
  printers,
  onClose,
  onSubmit,
}: {
  printers: Printer[];
  onClose: () => void;
  onSubmit: (payload: Record<string, unknown>) => void;
}) {
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    onSubmit({
      name: String(form.get("name")),
      material_type: String(form.get("material_type")),
      brand: String(form.get("brand")),
      color: String(form.get("color")),
      remaining_percent: Number(form.get("remaining_percent")),
      spool_grams: Number(form.get("spool_grams")),
      assigned_printer_id: String(form.get("assigned_printer_id")) || null,
    });
  };
  return (
    <Modal
      eyebrow="MATERIAL SHELF"
      title="Add a spool"
      copy="Track what you have and assign the spool to a printer when it is loaded."
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <label>
          Spool name
          <input name="name" placeholder="e.g. Black PETG" required />
        </label>
        <div className="form-split">
          <label>
            Material
            <select name="material_type" defaultValue="PLA">
              <option>PLA</option>
              <option>PETG</option>
              <option>ABS</option>
              <option>ASA</option>
              <option>TPU</option>
              <option>Other</option>
            </select>
          </label>
          <label>
            Brand
            <input name="brand" placeholder="e.g. Polymaker" />
          </label>
        </div>
        <div className="form-split">
          <label>
            Remaining %
            <input
              name="remaining_percent"
              type="number"
              min="0"
              max="100"
              defaultValue="100"
            />
          </label>
          <label>
            Spool grams
            <input
              name="spool_grams"
              type="number"
              min="1"
              defaultValue="1000"
            />
          </label>
        </div>
        <div className="form-split">
          <label>
            Color
            <input name="color" type="color" defaultValue="#8aaec9" />
          </label>
          <label>
            Assigned printer
            <select name="assigned_printer_id" defaultValue="">
              <option value="">On shelf</option>
              {printers.map((printer) => (
                <option value={printer.id} key={printer.id}>
                  {printer.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        {actions(onClose, "Add spool")}
      </form>
    </Modal>
  );
}
function FileModal({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  onSubmit: (form: FormData) => void;
}) {
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onSubmit(new FormData(event.currentTarget));
  };
  return (
    <Modal
      eyebrow="FILE LIBRARY"
      title="Upload a print file"
      copy="The original file is stored only in this local workspace. Supported formats are .gcode, .g, .gc, .3mf, and .bgcode (up to 512 MB)."
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <label>
          Display name
          <input
            name="name"
            placeholder="e.g. Pallet clips - batch 04"
            required
          />
        </label>
        <label>
          Print file
          <input
            name="file"
            type="file"
            accept=".gcode,.g,.gc,.3mf,.bgcode"
            required
          />
        </label>
        <label>
          Estimated minutes
          <input
            name="estimated_minutes"
            type="number"
            min="1"
            placeholder="90"
          />
        </label>
        <label>
          Recommended material
          <input name="material" placeholder="e.g. PETG - Black" />
        </label>
        <label>
          Compatibility notes
          <input
            name="notes"
            placeholder="e.g. Verified with the P1S profile"
          />
        </label>
        {actions(onClose, "Store file locally")}
      </form>
    </Modal>
  );
}

export default App;
