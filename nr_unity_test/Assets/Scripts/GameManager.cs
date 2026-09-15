using System;
using UnityEngine;

namespace NRAI.Game
{
    public class GameManager : MonoBehaviour
    {
        public static GameManager Instance { get; private set; }
        public int Score { get; private set; }

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
            Score += points;
            Debug.Log($"Score updated: {Score}");
        }
    }
}
