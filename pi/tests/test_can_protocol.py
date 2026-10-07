import unittest
from types import SimpleNamespace
from pi.shared.can_protocol import *


class ProtocolTests(unittest.TestCase):
    def test_command_bytes_and_lengths(self):
        for f in Function:
            data = encode_request(f, 60 if f == Function.SET_TIMER_INTERVAL else None)
            expected = bytes.fromhex({1: '0000080101', 2: '000008010200003c',
                                      3: '0000080103', 4: '0000080104', 5: '0000080105'}[f])
            self.assertEqual(data, expected)
            self.assertEqual(parse_request(data).function, f)
            for invalid in (data[:-1], data + b'\0'):
                with self.assertRaises(ValueError):
                    parse_request(invalid)

    def test_u24_boundaries(self):
        for value in (0, 5, 65535, 65536, 86400, 0xFFFFFF):
            self.assertEqual(decode_u24(encode_u24(value)), value)
        for value in (-1, 0x1000000, True, 5.5):
            with self.assertRaises(ValueError): encode_u24(value)
        for data in (b'', b'\0\0', b'\0'*4):
            with self.assertRaises(ValueError): decode_u24(data)
        for value in (5, 86400):
            self.assertEqual(parse_request(encode_request(Function.SET_TIMER_INTERVAL, value)).interval, value)
        for value in (0, 4, 86401, 0xFFFFFF):
            with self.assertRaises(ValueError): parse_request(bytes.fromhex('0000080102') + encode_u24(value))
        with self.assertRaises(ValueError): parse_request(bytes.fromhex('00000801ff'))

    def test_status_mappings(self):
        timer = {'enabled': True, 'active': True, 'interval_seconds': 86400}
        for name, code in PROCESSING_CODES.items():
            for source, source_code in SOURCE_CODES.items():
                for status, result_code in RESULT_CODES.items():
                    last = SimpleNamespace(source=source, status=status, decision='NOT_CALIBRATED',
                                           events=[SimpleNamespace(processing_status=name)])
                    a, b = encode_status(timer, {'busy': True, 'last': last}, True)
                    self.assertEqual(a, bytes.fromhex('0000f0010f015180'))
                    self.assertEqual(b, bytes((0,0,0xf0,2,source_code,result_code,code,0)))
                    self.assertEqual(decode_reply(b)['processing'], code.name)
        a, b = encode_status(timer, {'busy': False, 'last': None}, False)
        self.assertEqual(b, bytes.fromhex('0000f00200000000'))
        self.assertFalse(decode_reply(a)['busy'])
        last.events = [SimpleNamespace(processing_status=name) for name in PROCESSING_CODES]
        self.assertEqual(encode_status(timer, {'busy': False, 'last': last}, False)[1][6], Processing.MIXED)
        for i, cls in ((4, Source), (5, Result), (6, Processing), (7, Decision)):
            invalid = bytearray(b); invalid[i] = 255
            with self.assertRaises(ValueError): decode_reply(bytes(invalid))


    def test_verification_and_reserved_fields(self):
        for subtype, meaning in VERIFICATION_NAMES.items():
            self.assertEqual(decode_reply(bytes((0,0,1,subtype,8,1)))['meaning'], meaning)
        for data in ('0000f0011000003c', '0000f00100000004', '0000f00300000000',
                     '000001030801', '000001010301', '0000f00200000001'):
            with self.assertRaises(ValueError): decode_reply(bytes.fromhex(data))
