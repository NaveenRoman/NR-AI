using System;
using UnityEngine;

namespace NRAI.GameCore
{
    public class NetworkManager : MonoBehaviour
    {
        public bool IsConnected { get; private set; }
        public string ServerAddress { get; set; } = "127.0.0.1:7777";

        public void ConnectToServer()
        {
            IsConnected = true;
            Debug.Log($"[NetworkManager] Connected to {ServerAddress}");
        }

        public void Disconnect()
        {
            IsConnected = false;
            Debug.Log("[NetworkManager] Disconnected from server");
        }
    }
}
