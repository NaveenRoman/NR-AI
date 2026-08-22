using UnityEngine;

namespace NRAI.GameCore
{
    public class GameManager : MonoBehaviour
    {
        public static GameManager Instance { get; private set; }
        public int Score { get; private set; }
        public bool IsGameOver { get; private set; }

        private void Awake()
        {
            if (Instance == null)
            {
                Instance = this;
                DontDestroyOnLoad(gameObject);
            }
            else
            {
                Destroy(gameObject);
            }
        }

        public void AddScore(int points)
        {
            if (IsGameOver) return;
            Score += points;
            Debug.Log($"[GameManager] Score updated: {Score}");
        }

        public void TriggerGameOver()
        {
            IsGameOver = true;
            Debug.Log("[GameManager] Game Over!");
        }
    }
}
