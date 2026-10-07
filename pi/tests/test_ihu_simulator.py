import socket
import unittest
from unittest.mock import Mock
from pi.shared.can_protocol import Function
from tools.ihu_simulator import IhuClient, FRAME


def frame(hex_data):
    data = bytes.fromhex(hex_data)
    return FRAME.pack(0x320, len(data), data)


class SimulatorTests(unittest.TestCase):
    def client(self, replies):
        sock = Mock()
        sock.send.return_value = FRAME.size
        sock.recv.side_effect = replies
        return IhuClient(sock, output=lambda _: None)

    def test_status_sequence(self):
        client = self.client([frame(s) for s in ('000001010801', '0000f0010300003c',
                                               '0000f00201010100', '000001070801')])
        result = client.execute(Function.GET_STATUS)
        self.assertEqual(result['outcome'], 'success')
        self.assertEqual(result['reports']['timer']['interval_seconds'], 60)
        self.assertEqual(result['reports']['operation']['processing'], 'INCOMPLETE_TRIPLET')

    def test_timeout_prevents_retry(self):
        client = self.client([socket.timeout()])
        with self.assertRaisesRegex(TimeoutError, 'outcome unknown'): client.execute(Function.CAPTURE_NOW)
        with self.assertRaises(RuntimeError): client.execute(Function.GET_STATUS)
        client.sock.send.assert_called_once()

    def test_one_pending(self):
        client = self.client([])
        client._pending.acquire()
        with self.assertRaises(RuntimeError): client.execute(Function.GET_STATUS)
        client.sock.send.assert_not_called()

    def test_failure_and_rejection(self):
        client = self.client([frame('000001020801')])
        self.assertEqual(client.execute(Function.START_TIMER)['outcome'], 'rejected')
        client = self.client([frame('000001010801'), frame('000001080801')])
        self.assertEqual(client.execute(Function.START_TIMER)['outcome'], 'failure')

    def test_incomplete_status_or_bad_sequence_poison_session(self):
        for replies in (['000001010801', '000001070801'], ['000001070801'],
                        ['000001010801', '0000f002ffffff00']):
            client = self.client([frame(s) for s in replies])
            with self.assertRaises(ValueError): client.execute(Function.GET_STATUS)
            self.assertTrue(client.uncertain)
