using UnityEngine;

namespace NRAI.GameCore
{
    [RequireComponent(typeof(Rigidbody2D))]
    public class PlayerController : MonoBehaviour
    {
        [Header("Movement Settings")]
        [SerializeField] private float moveSpeed = 5.0f;
        [SerializeField] private float jumpForce = 8.0f;

        private Rigidbody2D _rb;
        private Vector2 _moveInput;

        private void Awake()
        {
            _rb = GetComponent<Rigidbody2D>();
        }

        private void Update()
        {
            float x = Input.GetAxisRaw("Horizontal");
            float y = Input.GetAxisRaw("Vertical");
            _moveInput = new Vector2(x, y).normalized;

            if (Input.GetButtonDown("Jump"))
            {
                Jump();
            }
        }

        private void FixedUpdate()
        {
            _rb.velocity = new Vector2(_moveInput.x * moveSpeed, _rb.velocity.y);
        }

        private void Jump()
        {
            _rb.velocity = new Vector2(_rb.velocity.x, jumpForce);
        }
    }
}
