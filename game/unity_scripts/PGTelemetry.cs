// Proving Ground: lightweight performance telemetry for the instrumented TrashCat build.
// Our own code, not Asset Store content. Every 5 s of real time it logs one line,
//   PGTELEM {"v":1,"ts":...,"scene":...,"frame_ms_p50":...,"mem_managed_bytes":...}
// which the runner collects from the game log (and Phase 3 forwards to Sentinel).
// It creates itself at startup, survives scene loads, and allocates nothing per frame.

using System;
using System.Globalization;
using System.Text;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.Scripting;

[Preserve]
public class PGTelemetry : MonoBehaviour
{
    const float k_WindowSeconds = 5f;
    // Enough for a 5 s window at 800 fps; frames beyond it are counted as dropped, not sampled.
    const int k_MaxSamples = 4096;

    readonly float[] m_FrameMs = new float[k_MaxSamples];
    readonly float[] m_Sorted = new float[k_MaxSamples];
    readonly StringBuilder m_Line = new StringBuilder(320);
    int m_Count;
    int m_Dropped;
    float m_WindowStart;

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    static void Bootstrap()
    {
        if (FindObjectOfType<PGTelemetry>() != null)
        {
            return;
        }
        GameObject host = new GameObject("PGTelemetry");
        DontDestroyOnLoad(host);
        host.AddComponent<PGTelemetry>();
    }

    void Awake()
    {
        m_WindowStart = Time.realtimeSinceStartup;
    }

    void Update()
    {
        // Unscaled: the game sets Time.timeScale to 0 while paused and tests may speed it up.
        if (m_Count < k_MaxSamples)
        {
            m_FrameMs[m_Count++] = Time.unscaledDeltaTime * 1000f;
        }
        else
        {
            m_Dropped++;
        }

        float now = Time.realtimeSinceStartup;
        if (now - m_WindowStart >= k_WindowSeconds)
        {
            Emit(now - m_WindowStart);
            m_WindowStart = now;
            m_Count = 0;
            m_Dropped = 0;
        }
    }

    void Emit(float windowSeconds)
    {
        int n = m_Count;
        Array.Copy(m_FrameMs, m_Sorted, n);
        Array.Sort(m_Sorted, 0, n);

        CultureInfo inv = CultureInfo.InvariantCulture;
        m_Line.Length = 0;
        m_Line.Append("PGTELEM {\"v\":1,\"ts\":\"")
            .Append(DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ", inv))
            .Append("\",\"t\":").Append(Time.realtimeSinceStartup.ToString("F2", inv))
            .Append(",\"scene\":\"").Append(Escape(SceneManager.GetActiveScene().name))
            .Append("\",\"window_s\":").Append(windowSeconds.ToString("F2", inv))
            .Append(",\"frames\":").Append((n + m_Dropped).ToString(inv))
            .Append(",\"dropped\":").Append(m_Dropped.ToString(inv))
            .Append(",\"frame_ms_p50\":").Append(Percentile(n, 50).ToString("F2", inv))
            .Append(",\"frame_ms_p95\":").Append(Percentile(n, 95).ToString("F2", inv))
            .Append(",\"frame_ms_p99\":").Append(Percentile(n, 99).ToString("F2", inv))
            .Append(",\"mem_managed_bytes\":").Append(GC.GetTotalMemory(false).ToString(inv))
            .Append(",\"time_scale\":").Append(Time.timeScale.ToString("F2", inv))
            .Append('}');
        Debug.Log(m_Line.ToString());
    }

    // Nearest-rank percentile over the sorted samples; 0 when the window had no frames.
    float Percentile(int n, int q)
    {
        if (n == 0)
        {
            return 0f;
        }
        int rank = (int)Math.Ceiling(q / 100.0 * n);
        return m_Sorted[Math.Max(1, rank) - 1];
    }

    static string Escape(string text)
    {
        return text.Replace("\\", "\\\\").Replace("\"", "\\\"");
    }
}
